# Adaptive frame polling

The supervisor processes one observation at a time. `INTERVAL_SECONDS` is the
minimum target start-to-start interval. Backpressure raises that target when
a complete cycle takes longer than its current budget. It does not queue
captures or overlap processing to catch up.

## Policy

The measured cycle includes stream lookup/capture, OCR, appearance and stint
analysis, database work/commit, prior timing-event import and retention cleanup. The
new timing decision is atomically queued afterward (a local file write), then
imported on the next cycle or graceful shutdown. Slow telemetry imports are
therefore included in the measured work budget. Decisions use monotonic elapsed time, so wall-clock changes
do not affect pacing.

- On an overrun, set the next target to the greater of **1.25 times the old
  target** and **1.25 times the elapsed cycle**. Round upward to 0.1 second.
- Hold the raised target until **12 consecutive successful cycles** each use
  at most **70% of the current budget**. Then reduce the target by **10%**,
  never below the configured minimum. Each further reduction needs another
  full stable streak. A busier cycle resets the streak.
- Capture/analysis errors and database failures increase the target by at
  least **50%**, with the same elapsed-time headroom rule. They reset recovery.
  Inactive-window and lock-contention skips do not train recovery.
- Cap the target at `BACKPRESSURE_MAX_INTERVAL_SECONDS=60`. If a cycle itself
  reaches or exceeds the cap, record `saturated` and wait a whole maximum
  interval **after** processing ends. This prevents immediate repeated retries
  when the configured cap cannot accommodate the workload.
- Normally wait only until this cycle's start plus the new target. There is no
  accumulated deadline or backlog from prior cycles. Stop signals interrupt
  the wait.

Settings:

```dotenv
INTERVAL_SECONDS=4
BACKPRESSURE_MAX_INTERVAL_SECONDS=60
BACKPRESSURE_STABLE_SAMPLES=12
```

The maximum must be at least the base interval and the stable-sample count must
be a positive integer. The headroom, recovery utilization and reduction factors
are fixed policy constants. Retain the effective interval across restarts;
changing the configured minimum clamps future budgets but does not discard an
existing raised budget. Recovery still requires the stable streak. If a manual base change finds the
old target equal to its old base (no raised pressure), the new base takes
effect immediately; an actually raised budget is preserved.

For example, a 5-second cycle while targeting 3 seconds raises the next target
to 6.3 seconds and leaves about 1.3 seconds before the next cycle. One fast
observation does not return it to 3 seconds. A 100-second cycle with a 60-second
maximum waits another 60 seconds before retrying.

The effective interval is passed into camera evidence continuity and saved as
`observations.details.polling_interval_seconds`. Clothing baselines are not
reset merely because the target changes; sustained evidence still uses actual
capture timestamps. Current-tester comparisons use the observed effective
interval for app-freshness checks, not only the configured minimum.

A fresh `change_pending` appearance cue requests a faster follow-up instead of
waiting for the normal recovery streak: the next target is the smaller of the
current budget and `max(base interval, cycle duration × 1.25)`, rounded up to a
tenth of a second. `follow_appearance_candidate` records this decision.
Processing overruns, failures and saturation take precedence, so captures stay
serial and retain headroom. This improves confirmation timing; it cannot recover
a handover whose appearance produced no detectable cue.

## Persistence and inspection

`polling_events` stores every decision with stream, observation ID, receipt
time, elapsed cycle, previous/next target, outcome, recovery streak and action.
The decision is also linked in `observations.details.polling` when that row is
available. Numeric counter/session history is not changed by pacing decisions.

Local state and pending timing events live in `data/polling/`. Events are
atomically queued before import, replayed in time order and keyed by UUID.
Database failure preserves local timing evidence and the latest state. Startup
chooses the newest local/database state, so a restart does not immediately
forget backpressure. Pending observation rows may be replayed by the regular
outbox separately; the timing event's observation UUID remains the linkage.
These records and local state are outside frame retention.

```sh
docker compose exec db psql -U folding -d folding -c "
SELECT recorded_at AT TIME ZONE 'Europe/Warsaw' AS warsaw_time,
       details->'state'->>'cycle_seconds' AS cycle_seconds,
       details->'state'->>'previous_interval_seconds' AS previous_interval,
       details->'state'->>'effective_interval_seconds' AS next_interval,
       details->'state'->>'stable_samples' AS stable_samples,
       details->'state'->>'action' AS action
FROM polling_events
WHERE stream_url='https://www.youtube.com/watch?v=vaiHWiExDXg'
ORDER BY recorded_at DESC LIMIT 30;"
```

Scheduler decisions are also emitted to `docker compose logs supervisor`.
If prior telemetry import fails, `prior_telemetry_queued` is true and local replay remains
pending; the observation database failure status is recorded independently.

## Schema-lock fix

The faster full-path benchmark exposed PostgreSQL deadlocks: schema/view
recreation ran inside every capture transaction and held locks during network
I/O. `ensure_schema()` now sets up schema in a separate committed transaction,
serialized by a short schema advisory lock, and caches success per connection
configuration in the process. Ordinary observation cycles no longer recreate
views. A failed setup is not cached. Restart the service to apply new schema
code. This changes lock lifetime, not existing data or rate calculations.

## Reproducing cadence tests

The benchmark now supports 3 and 4 seconds and a full production-path mode:

```sh
docker compose run --rm supervisor python benchmark_frame_intervals.py \
  --seconds 60 --interval 3 --mode serial --serial-checks 0
docker compose run --rm supervisor python benchmark_frame_intervals.py \
  --seconds 60 --interval 4 --mode serial --serial-checks 0
```

Serial mode includes fresh stream lookup and capture, all analysis and actual
PostgreSQL commits. It uses actual receipt timestamps on an isolated benchmark
stream. It starts no parallel cycles; a fixed-target overrun starts the next
cycle after completion. These fixed tests evaluate the base candidates;
the running supervisor adds the adaptive headroom policy above.

`--mode persistent` retains the earlier single-decoder experiment, which excludes
per-frame stream setup and is not the current production capture path. Do not
combine its analysis-only times with full-path timings as identical workloads.
The earlier tests were also affected by schema contention; corrected measurements
are preserved separately rather than replacing historical evidence.

```sh
docker compose run --rm -v "$PWD/proof-of-concept:/fixtures:ro" supervisor \
  python polling_integration_check.py
```

Unit tests cover overrun growth, hysteresis, recovery floor, failure backoff,
saturation cooldown and local restart state. The isolated-schema PostgreSQL
check verifies that schema locks are released before capture and timing state
survives database failure/replay, with observation linkage. All live benchmark
frames, observations and reports are retained under `data/benchmarks/`, with
copied reports/checksums in `proof-of-concept/test-data/2026-10-25-backpressure/`.
These are short-run measurements; network latency and scene-dependent work can
still produce later overruns, which is why adaptive pacing remains enabled.

## Baseline decision after the schema fix

The corrected 60-second full production-path tests ran with regular monitoring
active and had no database errors:

| Target | Completed cycles | Mean | p95 | Max | Overruns |
| --- | --- | --- | --- | --- | --- |
| 3 seconds | 20 | 2.52 s | 2.99 s | 3.15 s | 1 |
| 4 seconds | 15 | 2.86 s | 3.24 s | 3.62 s | 0 |

Choose **4 seconds as the base**, with backpressure handling later variability.
These short sequential runs show a practical starting point, not a hard laptop
limit. Their workloads also depend on network and scene conditions. Earlier
3/4-second tests with schema deadlocks are preserved and labelled pre-fix.
