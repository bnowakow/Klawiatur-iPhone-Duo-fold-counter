# Current tester pace comparisons

`compare_current_tester.py` compares the published **Aktualny Wynik Składającego**
`Złożenia na minutę` with our current anonymous presenter's recent pace and
average since their detected first accepted counter increase. It stores every
comparison in PostgreSQL `tester_comparisons` for analysis over time.

## Running

Fetch fresh public source data, archive it, and print folds/minute on labeled lines
(requires host `jq`; labels are blue and value/unit columns are aligned):

```sh
make docker-compare
```

The jq filter in `utilities/compare-pace.jq` shows the website's published
`Złożenia na minutę`, our current person's average since the recorded folding
start (`stint_fpm`), and the signed difference (ours minus website), rounded to
two decimal places. Stale, uncertain, missing, or invalid published values produce
an `unavailable` summary with the reason, rather than a jq error. Negative source
rates or measurement minutes remain invalid; they are never clamped or reused
from an older snapshot. Expected unavailable reports (script exit 2) are handled
successfully by the Make target; Docker failures and malformed JSON still fail.
This average is separate from recent pace;
the recorded start can still differ from the organizer's measurement window.

For the full JSON report, including recent pace and diagnostics:

```sh
docker compose run --rm official-data python compare_current_tester.py
```

Use the latest database source attempt without fetching:

```sh
docker compose exec -T official-data python compare_current_tester.py --stored
```

`--stream URL` selects another stream's app measurements. The website source is
still the public iPhone Duo current-tester table; this does not establish that
another stream shows the same test. Exit code 0 means a numerical comparison
was made; 2 means insufficient, stale, failed or uncertain evidence.

The `official-data` service also invokes the comparison after every archive run,
by default approximately every five minutes. Comparison failure is logged
separately and does not discard source archives or stop the stream supervisor.
Repeated calls for the same stream/source run return the stored comparison
instead of adding another row. Snapshots with unchanged published values are
flagged in `details.published_values_changed`; a new fetch is not proof the
organizer updated the score.

## Stored evidence

Each row stores the source run UUID, app session UUID, stream URL, comparison
time, status, published folds/minute, app recent/stint folds/minute, and signed
app-minus-site gaps in folds/minute. `details` retains:

- Source URL, fetch time, SHA-256 and source age.
- Published score, measurement minute, estimated final score and rate.
- App detection/folding start, accepted counter endpoint and receipt time,
  folding elapsed seconds, recent window duration and sample count.
- Current configured frame interval, attribution/freshness outcome and a hash
  of published values for detecting unchanged source snapshots.
- Candidate frame-interval diagnostics described below.

The current source is table 4 / the public current-score sheet documented in
[official-data.md](official-data.md). The parser looks up columns by their
published headers and supports Polish decimal commas. It does not assume the
organizer's published rounded rate equals score divided by displayed minutes.
Those inputs are retained separately.

To align receipt times, the app counter endpoint is the last eligible accepted
sample **at or before the source fetch time**, capped at the current stint's
last attributed counter time. Recent pace uses up to three minutes of eligible
samples in that stint; stint pace uses the stored folding-start counter and
time. Pending candidates and observations requiring attribution review are
excluded. Folds/minute is counter increase divided by elapsed seconds times 60.

Numerical gaps are NULL when the source is unavailable/unparseable, older than
two website intervals (minimum 60 seconds), or has a future receipt timestamp;
when there is no current stint, it began after the source fetch, attribution is
uncertain, or too few samples exist; and when the latest eligible app counter
is older than `max(60, 2.5 * INTERVAL_SECONDS)` relative to the source fetch.
Diagnostic raw app estimates can still appear in `details.app` on invalid rows;
only `status='compared'` rows should be used for gap analysis.

Both start times and windows can differ from the organizer's. Source fetch time
is not its original measurement time. There are no participant names in the
published table: `identity_verified` remains false, and a current-stint UUID is
not a proven identity match. A change in source tester is not automatically
matched to our stint. Interpret gap trends within a stint and inspect source
score/minute resets, changing source values and window differences before
claiming convergence. No adjustment to OCR or folding starts is made to force
agreement with the website.

## Observing the gap

```sh
docker compose exec db psql -U folding -d folding -c "
SELECT compared_at AT TIME ZONE 'Europe/Warsaw' AS compared_warsaw,
       session_id, status, official_fpm,
       round(recent_fpm::numeric,2) AS recent_fpm,
       round(stint_fpm::numeric,2) AS stint_fpm,
       round(stint_gap_fpm::numeric,2) AS stint_gap_fpm,
       details->'published'->>'minutes' AS published_minutes,
       details->'app'->>'folding_elapsed_seconds' AS our_folding_seconds,
       details->>'published_values_changed' AS source_values_changed
FROM tester_comparisons
WHERE stream_url='https://www.youtube.com/watch?v=vaiHWiExDXg'
ORDER BY compared_at DESC LIMIT 30;"
```

Compare absolute `stint_gap_fpm` across multiple changing source measurements
within the same app stint, together with both elapsed-time fields. A smaller
gap later can be consistent with boundary effects diminishing, but neither
source is ground truth. Repeated stale values or a tester switch do not prove
convergence. The full comparison history remains in the database.

## Evaluating frame-check intervals

The supervisor remains at its configured interval (currently four seconds with adaptive backpressure).
The script performs an **offline thinning experiment**, for target intervals
1, 2, 3, 4, 5, 10, 20, 30 and 60 seconds, on the accepted samples in each recent window.
It retains the first sample in each time bucket and the common first/last
endpoints, interpolates the counter between retained points, and compares
that reconstruction with the original accepted samples.

Targets shorter than the reference median gap (allowing 25% timing jitter)
are marked `finer_reference_required`, without invented error estimates.
Normal 10-second observations cannot establish the quality of 1/2/5-second
sampling. For supported targets it records retained/reference sample counts, largest actual gap,
mean absolute counter reconstruction error and maximum error. The target is
not a promise of exact timing; actual timestamps and gaps are retained. Equal
endpoints give the same aggregate window pace, so reconstruction error tests
the intermediate detail lost rather than pretending aggregate agreement proves
an optimal interval.

These diagnostics can support choosing a longer interval if several independent
stints keep adequate counter detail. They cannot measure events missed between
original frames, OCR errors already accepted, or handover detection accuracy.
They also do not benchmark capture runtime or system load. No interval is
recommended or automatically changed from one comparison. Collect enough
changing source values and stints before deciding.

## Validation

```sh
docker compose run --rm official-data python -m unittest discover \
  -s proof-of-concept -p 'test_*.py'
docker compose run --rm official-data python comparison_integration_check.py
```

The integration check uses a separate `comparison_test_…` PostgreSQL schema,
verifies known recent/stint gaps, idempotence, stale source/app, attribution
uncertainty and source failure, and preserves its result under
`data/test-evidence/comparisons/` (override with `COMPARISON_TEST_OUTPUT_DIR`).

## Dense live benchmark down to one second

```sh
docker compose run --rm official-data python benchmark_frame_intervals.py --seconds 60 --interval 1
```

The benchmark first times two captures through the current per-frame yt-dlp/
ffmpeg setup. It then keeps one HLS decoder open and samples 60 media seconds
at the selected interval (1, 2, 3, 4 or 5 seconds), passing each frame through the full supervisor analysis
and PostgreSQL commit path. It runs concurrently with regular monitoring.
Benchmark observations use an isolated `benchmark:interval-<interval>s:<uuid>`
stream (serial mode uses `benchmark:serial:<uuid>`) and do not update production
counter/presenter state.

It reports frame counts, accepted samples, mean/p95/max analysis plus database
time, cycles exceeding one second, CPU seconds, Python peak RSS and actual
arrival gaps. CPU time includes the benchmark Python process and its children;
it excludes CPU consumed by PostgreSQL and the regular supervisor. Memory
units are platform-specific (`ru_maxrss`, KiB inside Linux Compose).

Source frames, per-frame timing/observation logs and results remain under
`data/benchmarks/persistent-interval-<interval>s-<uuid>/`
(older runs used `interval-<interval>s-<uuid>/` or `one-second-<uuid>/`).
Serial mode uses `serial-interval-<interval>s-<uuid>/`. These directories bypass
ordinary capture pruning, but their images remain subject to the global 20 GB cap.
The result is also stored in PostgreSQL `frame_interval_benchmarks`. Persistent-mode
timestamps are nominal media positions at the configured interval anchored at
receipt of the first frame; serial mode uses actual receipt times. Neither is
an authoritative broadcaster UTC time. The fixture flag identifies persistent-mode
rows. HLS segments may arrive in bursts; one media frame/second is different
from receiving fresh video every wall-clock second.

This is a short performance/counter-detail experiment, not a thermal or long-run
stability test. If processing stays comfortably under the target interval, the laptop
may sustain the requested media sampling with a persistent decoder.
The production supervisor still starts capture processes for each observation;
its measured capture overhead must be addressed before deploying such a rate.
The benchmark does not automatically change `INTERVAL_SECONDS`.

Use `--interval 2 --serial-checks 0` or `--interval 5 --serial-checks 0` to
evaluate those intervals without repeating setup-path captures. The benchmark
accepts no interval below one second. Compare `analyses_over_target_interval`
with the p95/max timing values; average throughput alone does not establish
regular deadlines. Benchmark run duration, accepted/failed counter samples and
source transport bursts remain material limits.

### Initial laptop results (2026-10-25)

With the normal services running, three 60-media-second persistent-capture
benchmarks processed all requested frames:

| Target interval | Frames | Mean analysis + DB | p95 | Max | Cycles exceeding target |
| --- | --- | --- | --- | --- | --- |
| 1 s | 60 | 0.79 s | 1.55 s | 3.33 s | 6 |
| 2 s | 30 | 0.91 s | 2.83 s | 4.06 s | 2 |
| 5 s | 12 | 1.04 s | 1.54 s | 3.64 s | 0 |

The current restart-per-frame capture path separately took 2.03 and 2.09 seconds
before analysis. These timings favor **5 seconds as a practical next candidate**,
with a persistent decoder for faster sampling. One-second aggregate throughput
was possible, but occasional stalls and transport bursts prevent a claim of
consistent one-second updates. Hardware alone is not shown to be the limiting
factor. No interval change was made to regular monitoring. These short tests
must be extended across more scene conditions before claiming reliability.

All frames, accepted/rejected observations, results, hardware context and
checksums are retained; see
[benchmark evidence](../proof-of-concept/test-data/2026-10-25-comparisons/README.md).

### Follow-up: 3/4 seconds and backpressure

The earlier persistent tests above predate the schema-lock fix. New tests of
the actual production capture path measured 20 cycles at a 3-second target
(one overrun, max 3.15 s) and 15 at a 4-second target (no overruns, max 3.62 s).
The configured base is now **4 seconds**, with adaptive backpressure. See
[the scheduling policy and full evidence](backpressure.md). Comparison freshness
uses the effective interval recorded with each observation.
