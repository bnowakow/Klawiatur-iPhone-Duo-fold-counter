# Project context for future work

## Morning camera and counter recovery (2026-10-26)

The morning main view shifted; `presenter_main_morning` adds a separate static
background reference and clothing-only crop, while preserving old layout profiles.
Original empty-chair/main/inset captures and expected counters are preserved in
`proof-of-concept/test-data/2026-10-26-morning-resumption/`. Fragmented last digits
need stronger segment joining; relaxed red bounds avoid clipping pale LED cores.
Closed-night resumption now requires two plausible positive readings to refresh
the pace anchor even if overnight pause samples kept the old anchor recent.
This prevents early morning activity from staying `suspect_slow` against an
overnight interval. Absolute bounds and decreasing-counter checks remain active.

## Morning resumption readiness (2026-10-26)

The live overnight state retained `appearances.attribution_review_required=true`.
The supervisor now clears that old-stint hold only after a known presenter layout
and accepted counter increase establish `activity_resumed_after_night_pause`.
A pending appearance cue is retained with resolution
`resolved_on_activity_resumption_review_required`; it cannot reapply the old hold
to the new stint. A second accepted increase within the new stint establishes
its folding start. Stationary break frames continue to leave the previous stint
closed. Regression coverage includes a simultaneous new clothing cue and an
isolated PostgreSQL test with persisted review state.

Verified real break outcome (2026-10-25): final display 63,333; prior stint
`1ce68ce5-5e1c-47b0-bb47-e1a07ad098dc` is closed with estimated stop
22:00:57.733 Warsaw and confirmation 22:15:15.440. Its attributed counter remains
63,330. A decoder-recovery timestamp initially gave a later stop estimate;
`refine_break_endpoint.py` corrected it using source observation
`4771be03-dab0-47fc-9062-d4fa2e3d3cc4`, preserving the confirmation and original
history and appending audit observation `a33950da-31d9-43fa-b850-3847a9fc3c13`.
This is a sampled endpoint estimate requiring review, not exact identity or
physical stop-time verification. Runtime guards now reject a saved last-increase
timestamp later than the stint's last attributed counter observation. The
real-frame/PostgreSQL integration check covers the audited correction tool too.

Latest break-scene fix (2026-10-25): the real overnight camera view initially
failed OCR and layout matching, so no real stop was logged by the first night
inactivity implementation. `camera_views.detect_break_scene` now matches a
calibrated break-sign template (with ink-pixel checks) and empty background,
returning `night_break_detected` / `scene_kind=scheduled_break` without creating
a clothing baseline or person stint. `read_counter.py` adapts horizontal and
vertical segment thresholds to thinner LED strokes and reads 63,333 correctly.
Repeated break-scene reads may accept a final increment within `COUNTER_SLACK`
as `accepted_break_boundary`, without updating learned pace or attributing the
extra folds to the old stint. A confirmed stationary break closes the prior
stint with `night_break_sign_counter_stopped_review_required` and
`break_scene_verified=true`, preserving its previous trusted last-increase
time as the stop estimate. Sign matching does not identify people or parse
arbitrary board text. New files: `test_break_scene.py`,
`break_scene_integration_check.py`, `camera-profiles/night-break.json`, and
the immutable `test-data/2026-10-25-night-break/` regression dataset.

Latest session-end addition: overnight inactivity handling is documented in
[Night stop times](README.md#night-stop-times). `presenter_sessions.py` now
closes a stint after three minutes of fresh unchanged accepted readings in a
configurable 21:30–06:30 Warsaw window. It stores an estimated `ended_at` plus
separate confirmation/evidence in `presenter_session_ends`. This is a
review-required folding-stop estimate; disappearance is not independently
verified. Capture gaps/unreadable frames interrupt confirmation, and the clock
alone never ends an active stint. A stationary counter does not reopen it;
accepted activity resumption in a known layout starts a fresh anonymous stint.
Configuration: `NIGHT_STOP_START`, `NIGHT_STOP_END`, `NIGHT_STOP_IDLE_SECONDS`.
Regression coverage: `test_night_stop.py`, `night_stop_integration_check.py`.

Last reviewed: **2026-10-25**, Europe/Warsaw. This is a handoff snapshot,
not a live status page. Read this first, then [README.md](README.md) for
operating commands and the implementation for exact current behavior.

## Objective and user requirements

Monitor the public livestream:
<https://www.youtube.com/watch?v=vaiHWiExDXg>.

- Capture a fresh frame on a configurable schedule; the current minimum target
  is **4 seconds**, with adaptive backpressure (earlier targets were 30 and 10 seconds).
- Read the physical folding counter, including values above **1,000,000**.
- Flag likely false reads using monotonicity and a relatively steady pace.
- Flag possible presenter handovers for human verification. Presenters are
  expected to change roughly hourly. The user expects an overnight folding
  pause around **22:00–06:00 Europe/Warsaw** (approximate, supplied 2026-10-25).
  This is planning context, not evidence of a handover or an exact capture window.
- Store observations, diagnostics and persistent analysis state in PostgreSQL
  running in Docker. The application also runs in Docker.
- Preserve previous camera-angle evidence and regression datasets as new
  layouts are supported.

Presenter checks use clothing appearance and background layout cues. They do
not recognize people or compare biometric identity. A possible handover is a
review request, not proof that the person changed. An hour passing is not
evidence of a change.

## Historical snapshot (20:47, before later additions)

At that inspection, the original two Compose services were running, and PostgreSQL was healthy.
The current Compose file has three services; these rows and deployment observations
are historical evidence, not the current deployment or validation status.
Recent real-stream rows were:

| Warsaw time, 2026-10-25 | Raw / accepted counter | Counter status | Layout | Clothing status |
| --- | --- | --- | --- | --- |
| 20:46:11 | 60,126 | accepted | presenter_inset | no_clothing_change_detected |
| 20:46:41 | 60,148 | accepted | presenter_inset | no_clothing_change_detected |
| 20:47:11 | 60,167 | accepted | presenter_inset | no_clothing_change_detected |

Previously, the saved original frames were visually checked and read by the
script as **58,857** and **59,062**. Live logging also crossed 60,000 successfully.
Synthetic tests cover 999,999 → 1,000,000, narrow 1 digits, 1,000,001,
1,234,567, 1,999,999, and 10,000,000. This is limited validation, not an
accuracy guarantee for all future footage.

At that stage, validation passed **17 unit tests** in Docker and the
PostgreSQL integration check. That check produced five isolated observations
and verified inserts, state reloads, spike rejection, and database-outage replay.
At that snapshot there had not yet been a manually verified real handover
evaluation. The reviewed 26 October 07:00 dark-clothing transition now has
saved regression frames in `proof-of-concept/test-data/2026-10-26-dark-clothing-handover/`.

**Deployment gap at this snapshot:** the host's `supervisor.py` and the running
image's `/app/supervisor.py` had different SHA-256 hashes. `camera_views.py`
matched. Do not assume the running image includes every source change,
including the latest unknown-camera evidence preservation work. Rebuild and
verify before relying on newly added behavior:

```sh
docker compose up -d --build
docker compose ps
```

Recheck live database rows after rebuilding. Documentation changes alone do
not require a container rebuild.

## Code map

| File / directory | Responsibility |
| --- | --- |
| `supervisor.py` | Configuration, capture, pace validation, orchestration, SQL schema, transactions and observation outage queue |
| `polling.py` | Adaptive scheduling, restart state and timing-event replay |
| `frame_retention.py` | Anomaly windows, SQLite ledger and global image quota |
| `presenter_sessions.py` | Stints, appearances, night endpoints and SQL rate views |
| `ipduotest_pl.py` | Published-site archive and queued imports |
| `compare_current_tester.py` | Source-aligned pace comparisons and cadence diagnostics |
| `benchmark_frame_intervals.py` | Isolated persistent-decoder and full-path cadence benchmarks |
| `refine_break_endpoint.py` | Preview/apply an audited correction of a verified break endpoint |
| `Makefile`, `utilities/` | Docker upgrade, comparison output and Codex commit helpers |
| `proof-of-concept/read_counter.py` | Find red digit groups and decode seven-segment patterns |
| `proof-of-concept/detect_handover.py` | Clothing histograms and sustained-change state machine; also a standalone comparison CLI |
| `proof-of-concept/camera_views.py` | Select calibrated layouts and keep clothing baselines separate by layout |
| `proof-of-concept/camera-profiles/layouts.json` | Active background reference features and layout crop coordinates |
| `compose.yaml`, `Dockerfile` | PostgreSQL 17 and Python 3.12 application with FFmpeg |
| `.env.sample` | User-facing Compose configuration defaults |
| `integration_check.py` | Real database checks using saved fixtures and an isolated stream key |
| `proof-of-concept/test_*.py` | OCR, handover, camera-layout and supervisor regression tests |
| `proof-of-concept/test-data/` | Preserved historical evidence and checksum manifests |
| `data/` | Ignored runtime images, camera-review archive, and outage queue |

## One polling cycle

1. Connect to PostgreSQL. On first use of a connection configuration in the
   process, ensure the SQL schema in a separate committed transaction.
2. Acquire a transaction advisory lock keyed by stream URL; a competing
   supervisor skips the cycle. Replay queued observations idempotently and
   load the stream's state from PostgreSQL.
3. Check the configured active-hour window. Inactive checks are logged without
   capture or analysis.
4. Resolve fresh media metadata using `yt-dlp`; require a live stream. Choose
   the highest HLS format up to 1080p. FFmpeg extracts one frame near the live
   edge, with JPEG quality explicitly set to 2.
5. Hash the saved frame. An exact repeat of the preceding frame is logged as
   `duplicate_frame` and contributes no counter or clothing evidence.
6. Run counter OCR and pace validation. Independently select a camera layout
   and update its clothing-change evidence.
7. Insert the observation and update model state in the same transaction.
8. Prune ordinary frames outside anomaly windows and the ten-capture buffer,
   then enforce the global image cap. Record the adaptive scheduling decision
   and wait for the next cycle. Prior timing-event replay is included in the
   measured work budget.

Polling starts immediately and uses a monotonic clock. Work is serial; slow
cycles increase the next target interval rather than overlap or build a backlog. Metadata resolution has a
50-second process timeout and frame extraction a 40-second timeout, so an
attempt can exceed the configured polling interval.

`observed_at` is local frame receipt time stored as a timezone-aware timestamp,
not the original broadcast time. Streaming latency and capture jitter remain.

## Counter recognition and validation

OCR detects saturated red pixels, joins nearby segments, finds aligned digit
groups, and samples seven segment regions per digit. It has no fixed five- or
six-digit cap. Unknown patterns yield a null numeric value. The narrow shape
of a 1 has a dedicated heuristic. At least three illuminated digits are
required by the current locator.

Preserve original input pixels. An earlier fixture implementation re-encoded
images through Pillow's default JPEG quality and changed verified OCR results
to an incomplete or unreadable value. Fixture handling now copies original
bytes; live capture has no extra JPEG compression before analysis.

The validator retains an accepted anchor, up to 20 recent accepted positive
rates, and an optional provisional sample. For a normal interval `dt`:

```text
if recent positive rates:
  expected = median(recent positive rates)
  low_rate, high_rate = expected * (1 - RATE_TOLERANCE), expected * (1 + RATE_TOLERANCE)
else:
  low_rate, high_rate = EXPECTED_RATE_MIN, EXPECTED_RATE_MAX
lower = max(0, low_rate * dt - COUNTER_SLACK)
upper = min(MAX_RATE * dt + COUNTER_SLACK, high_rate * dt + COUNTER_SLACK)
```

| Counter status | Meaning |
| --- | --- |
| `provisional` | Initial or post-gap sample waiting for another plausible sample |
| `accepted_rebased` | Two plausible samples establish an initial or post-gap anchor; rate history resets |
| `accepted` | Nondecreasing value within the pace envelope |
| `accepted_paused` | Unchanged counter; pause or stalled video is possible; zero rates do not train the median |
| `accepted_break_boundary` | Three matching calibrated break-scene reads confirm a small final increment within slack; no pace training or presenter attribution |
| `suspect_slow` | Positive increase below the lower pace bound; anchor stays unchanged |
| `rejected` | Decrease, implausible increase, or non-increasing timestamp; anchor stays unchanged |
| `unreadable` | Unknown digits or no usable counter value; anchor stays unchanged |
| `not_analyzed` | Exact duplicate frame |

After 600 seconds since the accepted anchor, two plausible samples can rebase
the pace model. The old anchor still blocks decreases and increases above the
absolute maximum pace across the full gap. An actual physical counter reset
needs an intentional manual state correction; it is not silently accepted.
Initial/rebase confirmation uses the configured initial rate range plus slack
and the absolute maximum; it does not reuse the previous learned median.
Rebased readings remain explicitly less certain than uninterrupted checks.

Always retain raw readings and reasons. Do not train on rejected readings or
replace suspicious readings with fabricated extrapolated values.

## Layouts and possible handovers

Two presenter layouts are calibrated, plus a separate `night_break` scene
profile that bypasses clothing/stint creation (see the night-stop notes above):

- `presenter_main`: presenter is the main image; clothing ROI defaults to
  normalized `[0.48, 0.51, 0.65, 0.62]`.
- `presenter_inset`: overhead view is the main image and the presenter is in
  the bottom-right inset; clothing ROI is `[0.818, 0.833, 0.862, 0.876]`.

Layout selection compares small background patches with saved reference
features. The best distance must be at most 0.10 and beat the next candidate
by at least 0.025. These are calibrated thresholds, not general scene detection.

Each layout keeps a separate clothing baseline and pending-change history.
The crop's HSV histogram ignores hue for low-saturation fabric; total variation
distance ranges from 0 to 1. Distance at least 0.45 sustained for 60 seconds
flags `possible_handover_review_required`. At an uninterrupted four-second
cadence, 16 changed samples span the required minute. The permitted gap is
`max(2.5 * effective polling interval, 25)` seconds.

Switching layouts resets pending continuity and never compares clothing from
different layouts. Returning to a layout reuses its baseline but starts fresh
persistence evidence. A first visit creates a baseline. Profile/configuration
changes and a manual reset clear the per-view baselines; changing only the
polling interval preserves baselines.

Unknown or ambiguous layouts return `camera_view_unknown_review_required`,
without a clothing score or baseline update, and reset pending continuity.
Current source archives their frames and metadata outside normal retention in
`data/camera-review/<observation-id>/`. Supporting a new layout requires a coding
and calibration session; the supervisor does not modify its own algorithm.

See [the evidence-preservation procedure](proof-of-concept/test-data/README.md)
before adding profiles. Keep original captures, derived evidence, calibration
snapshots and checksum manifests unchanged. Add new dated datasets and tests;
do not overwrite historical evidence to make a new implementation pass.

## Configuration reference

Active configuration is stored in the repository-root `.env` on the host,
ignored by Git and excluded from Docker image builds. Both `supervisor` and
`ipduotest-pl` use Compose `env_file` and a read-only bind mount of that file
at `/app/.env`. There is no separate authoritative copy inside a container.
Create `.env` from `.env.sample` on a new checkout before starting Compose.
Edits require recreating the affected application containers to reload their
environment; the Python processes do not hot-reload the mounted file.

Copy `.env.sample` to `.env` to override Compose defaults. Current defaults:

| Setting | Default / unit |
| --- | --- |
| `STREAM_URL` | YouTube URL above |
| `INTERVAL_SECONDS` | 4 seconds, minimum target |
| `BACKPRESSURE_MAX_INTERVAL_SECONDS` | 60 seconds |
| `BACKPRESSURE_STABLE_SAMPLES` | 12 consecutive light cycles |
| `EXPECTED_RATE_MIN` | 0.4916666666666667 folds/second (29.5/min) |
| `EXPECTED_RATE_MAX` | 0.945 folds/second (56.7/min) |
| `MAX_RATE` | 5 folds/second |
| `RATE_TOLERANCE` | 0.5 fractional deviation |
| `COUNTER_SLACK` | 5 folds |
| `REBASE_AFTER_SECONDS` | 600 seconds |
| `CLOTHING_ROI` | `0.48,0.51,0.65,0.62`, normalized left/top/right/bottom |
| `CLOTHING_THRESHOLD` | 0.45 histogram distance |
| `HANDOVER_CONFIRM_SECONDS` | 60 seconds |
| `NIGHT_STOP_START`, `NIGHT_STOP_END` | `21:30`, `06:30` in `TIMEZONE` |
| `NIGHT_STOP_IDLE_SECONDS` | 180 seconds |
| `IMAGE_STORAGE_LIMIT_BYTES` | 20000000000 (20 GB decimal) |
| `IPDUOTEST_PL_INTERVAL_SECONDS` | 300 seconds after each collector run |
| `TIMEZONE` | `Europe/Warsaw` |
| `ACTIVE_START`, `ACTIVE_END` | Both `00:00`; equal values mean all day |
| `POSTGRES_PORT` | 54329 on host localhost |

The expected overnight folding pause is approximately 22:00–06:00 Warsaw time.
Capture remains enabled all day; approximate break times do not automatically
change `ACTIVE_START`/`ACTIVE_END`. A paused counter during this window is
expected context, not proof of capture failure or a presenter change.
When making the polling interval much longer, review the 600-second rebase
window too: consecutive provisional samples further apart than that window
cannot establish an anchor.

The image uses `/data` for `DATA_DIR`. Local Python defaults to `data/`.
`DATABASE_URL` can override the Python database connection; otherwise standard
`PGHOST`, `PGDATABASE`, `PGUSER`, and `PGPASSWORD` variables apply. Compose
explicitly provides those variables. A host environment variable is not
automatically passed into a container unless Compose/run configuration does so.
Keep real credentials in local configuration, not this document.

## Persistence and failure behavior

`observations` is append-oriented history keyed by a UUID, with stream URL,
timestamp, overall status, raw/accepted counters, counter reason/rate, clothing
status/score, image path and JSONB diagnostics. `details` includes raw digit
boxes/segment occupancies, pace verdict, layout selection and its distances,
clothing ROI/tracking, and baseline observation ID where available.

`monitor_state` has one row per stream URL. Its JSONB state contains
`counter`, `clothing`, `presenters`, `appearances`, `break_counter_confirmation`
when present, `last_observed_at`, and `last_frame_hash`. Clothing state contains the
configuration signature, per-layout `views`, and `last_view`. Database history
and state survive application restarts in the named `postgres_data` volume.

Every attempted scheduled cycle is logged, including capture errors, inactive
hours and duplicates. Lock-contention skips are printed to stdout only.
On database failure, observations are queued in `data/outbox/` as UUID-named
JSON files. They are replayed with `ON CONFLICT DO NOTHING` and removed after
commit. No capture starts without a working database connection. If the
connection fails after analysis, its observation is queued but uncommitted
model changes are queued alongside the observation and restored on replay. Use separate outbox/data directories for
independent streams.

Image paths in PostgreSQL are container paths such as `/data/frames/<id>/frame.jpg`;
the corresponding host path is `data/frames/<id>/frame.jpg`. Images and database
records have separate retention. All captures are kept for two hours; older ordinary
images are deleted outside anomaly windows and a ten-frame lookbehind buffer. Review archives bypass ordinary
pruning, but all images under `data/` remain subject to the global 20 GB cap.
Database history and repository regression fixtures are outside that image quota.
Back up the database volume and the persistent evidence separately.

## Resume and verify

Use `make docker-upgrade` after code, dependency, or `.env` changes. It builds
both application images, ensures the database service is ready, and recreates
`supervisor` and `ipduotest-pl` with the latest configuration, preserving data.

Run commands from this repository's root. Docker Desktop must be running and
the host awake for polling to continue.

```sh
docker compose ps
docker compose logs --tail=10 supervisor
docker compose up -d --build
docker compose run --rm supervisor python -m unittest discover -s proof-of-concept -p 'test_*.py'
docker compose run --rm -v "$PWD/proof-of-concept:/fixtures:ro" supervisor python integration_check.py
```

The integration check uses a unique `integration:` stream key and temporary
artifacts. Its five database rows remain as test history. Always filter by the
real stream URL when inspecting production readings:

```sh
docker compose exec db psql -U folding -d folding -c "
SELECT observed_at AT TIME ZONE 'Europe/Warsaw' AS warsaw_time,
       raw_counter, accepted_counter, counter_status, counter_reason,
       clothing_status, clothing_score,
       details->'camera_view'->>'view' AS camera_view, frame_path
FROM observations
WHERE stream_url = 'https://www.youtube.com/watch?v=vaiHWiExDXg'
ORDER BY observed_at DESC LIMIT 20;"
```

For a reviewed historical handover missed by automatic tracking, preview an
audited split using accepted boundary observations belonging to the still-current
old stint:

```sh
docker compose run --rm supervisor python repair_presenter_handover.py \
  --old-session OLD_UUID --last-old-observation LAST_OLD_UUID \
  --first-new-observation FIRST_NEW_UUID
```

After reviewing the preview, stop the supervisor, repeat with `--apply`, and
start the supervisor with the rebuilt image. The repair transaction stores
original state/observations/comparisons, closes the old stint at the last reviewed
old sample, installs the new clothing baseline and reassigns later samples.
Counter increases between the boundary samples remain unattributed. Historical
mixed-stint comparisons are marked superseded; duplicate repair is refused.
It requires a subsequent accepted increase proving folding in the new stint.

Supplemental garment-print crops supplement the shoulder histograms using the
same threshold and sustained-change rules. Dark garments with similar prints
can still hide a handover; these cues do not identify people.
Compatible detector upgrades preserve existing baseline observation IDs and
reconstruct supplemental garment features from those original frames. Missing
source evidence or changed crop geometry produces
`clothing_baseline_review_required` and holds stint attribution until reviewed;
the current shirt is never silently acknowledged as the previous baseline.
Threshold/confirmation changes restart pending continuity without replacing the
baseline. Pending appearance cues request a faster next sample with processing
headroom; errors, overruns and saturation retain their backpressure protections.
`docker compose down` stops services while preserving the named volume.
Adding `-v` deletes the database volume; it is not a routine restart command.

## Remaining work and practical limits

1. Evaluate a real handover with saved before/during/after footage and manual
   labels. Measure missed handovers and false alarms before claiming reliability.
2. Calibrate additional layouts from preserved unknown-camera evidence and
   retain regression coverage for every old layout and transition.
3. Tune pace/appearance thresholds against more real footage, especially digit
   transitions, partial segments, lighting changes, obstructions, and overnight
   resumption. Similar clothes can hide a handover; a clothing change by the same
   presenter can trigger one. A first baseline after a switch can hide a change.
4. Distinguish breaks, empty-chair scenes, frozen video and ordinary pauses more
   reliably. Exact frame hashing alone does not establish video freshness.
5. Consider a review UI, review acknowledgements, alerts and explicit counter
   reset handling if the user requests them. None is currently implemented as
   a product workflow. No external notification service is configured.
6. Add versioned schema migrations and deployment/version tracking before
   evolving persistent database/state formats. Current schema setup creates missing tables/indexes and replaces SQL views;
   it does not migrate existing table columns.

Keep these constraints visible when continuing the project. Do not report an
appearance cue as confirmed identity, an accepted counter as guaranteed OCR,
or a source-code change as deployed without checking the running container.

## Presenter stint tracking (added 2026-10-25)

`presenter_sessions.py` maintains anonymous provisional stints.
`presenter_sessions` stores `detected_at` (first sustained-change sample),
`confirmed_at`, and `folding_started_at` (first accepted increase within that
stint), counter baselines/endpoints and evidence observation IDs. Pending
candidates retain their counters in `monitor_state`; confirmed cues close the
old stint at detection time and promote the candidate. The new shirt is
automatically adopted once and other view baselines are cleared for relearning.
Manual baseline reset now closes/opens a stint. First observation after upgrade
is marked `first_observed`; no historical arrival time is invented.

`presenter_session_rates` exposes rough nominal and folding-start hourly rates,
fold deltas and durations. See README for formulas, queries and limitations.
Session snapshots save transactionally with observations. Outbox replay restores
completed model state and uses version guards to ignore stale session snapshots.
Run `session_integration_check.py` in Compose with `/fixtures` mounted; it checks
start times, closure, SQL rates and outage/replay, preserving evidence under
`/data/test-evidence/presenter-sessions/` by default. Unit tests also cover real
layout crops with a synthetic shirt change, ensuring only one stint is created.

## ipduotest-pl’s public website archive (added 2026-10-25)

The user requested all published test data from https://ipduotest.pl/ and its
context. `ipduotest_pl.py` collects homepage HTML, all configured public
Sheetable sheet views and same-site homepage images, independently of the
stream supervisor. The initial source set is three unique Google sheet/tab
views (current score, Top 5, overall history) plus the chart and homepage.
Table configurations 1 and 2 reference the same history sheet; fetch once,
retain both aliases. No participant names are published in those tables.

Compose service `ipduotest-pl` starts immediately and then waits
`IPDUOTEST_PL_INTERVAL_SECONDS=300` after each run. Schema: `ipduotest_pl_runs`,
`ipduotest_pl_resources`, `ipduotest_pl_blobs`; views: `ipduotest_pl_latest`
and `ipduotest_pl_latest_success`. Exact response bytes, parsed cells, source
configuration, timestamps and errors are retained. Local content-addressed
blobs and pending/imported manifests live under `data/ipduotest-pl/`. Ordinary
frame pruning excludes them; the global image cap can evict local image blobs.
Original response bytes already imported into PostgreSQL remain stored. Database failure queues files and replay is idempotent.

See `docs/ipduotest-pl.md` for verified source links, field meanings, source
inconsistencies, freshness checks and SQL. Initial fixtures and regression
evidence are preserved in `proof-of-concept/test-data/2026-10-25-ipduotest-pl/`.
Published times lack an explicit timezone and remain source strings. Do not
interpret scheduled hourly slots as detected handovers or merge ipduotest-pl
scores into accepted OCR. Chart raw numeric points, private/unreferenced tabs,
and pre-collection edit history are unavailable from the homepage.

## Brief appearances and folding attribution (21:00 transition)

The user clarified that a brief olive-jacket appearance did not start folding.
Keep arrival/appearance evidence separate from accepted counter increases.
`presenter_appearances` now retains pending, interrupted, brief and sustained
clothing cues even without folding. Interrupted/cleared cues hold attribution
to the previous stint until sustained confirmation or manual baseline reset.
`no_accepted_increase_observed` is not proof of zero folds. Images and event
metadata are archived in `data/presenter-review/` outside ordinary capture
pruning, with images still subject to the global 20 GB cap.
Fine 16-bin luminance comparisons at distance >= 0.60 supplement coarse colour
cues for gray versus white fabric, with the same continuity requirements.
Original real transition frames and hashes are preserved in
`proof-of-concept/test-data/2026-10-25-brief-appearances/`. Camera switches still
interrupt continuity and must not be interpreted as confirmed identity changes.

## Faster polling and recent pace

Captures now target a four-second minimum with adaptive backpressure. Slow
cycles increase the target without overlapping captures.
Handover confirmation remains 60 seconds; changing only the interval preserves
clothing baselines. `presenter_recent_pace` separates a rolling three-minute
accepted-counter pace from the recorded stint average and exposes actual
window duration, sample count and last measurement time. A late recorded stint
boundary still makes the whole-turn average uncertain.

## Current scope: database collection first

The user clarified that this is an independent alternative to ipduotest-pl.
For now, retain all needed data in PostgreSQL and defer the rest. Keep our
stream observations and anonymous presenter evidence separate from archived
ipduotest-pl values. Do not build a UI, choose a new ranking/projection policy,
or silently equate scheduled source slots with our detected stints.
`docs/data-storage.md` maps future totals, starts, durations, rates, rankings,
source projections and event notes to stored inputs, and states gaps explicitly.
Verified live storage on 2026-10-25: observations, presenter stints with folding
starts, and all five public-source resources are present in PostgreSQL.

## Initial rate range calibration

The user requested a range in place of scalar `EXPECTED_RATE`, seeded from the
site's published `Złożenia na minutę`. A fresh successful import contained 27
completed history rows with positive rates: min 29.5, max 56.7 folds/minute.
Python defaults, Compose, `.env.sample` and the local `.env` use
`EXPECTED_RATE_MIN=0.4916666666666667` and `EXPECTED_RATE_MAX=0.945` in
folds/second. The initial second-sample/rebase check also uses these bounds.
Unchanged counters still permit pauses. Learned positive median rates retain
the existing tolerance envelope; the initial min/max do not get extra tolerance.
Snapshots and derivation are preserved under
`proof-of-concept/test-data/2026-10-25-rate-range/`. No automatic configuration
updates occur from later website imports. Existing counter/session state is
preserved at deployment; the seed applies when rate history is empty.

## Current-tester comparison monitoring

The user requested a script and ongoing observation of whether the folds/minute
gap narrows with stint duration, to inform frame cadence later.
`compare_current_tester.py` refreshes public data by default; `--stored` reads
the latest archived attempt. The `ipduotest-pl` service invokes it after
each refresh and persists `tester_comparisons` rows keyed by stream/source run.
It aligns accepted app samples to source fetch time, records recent and stint
pace and source score/minutes, and suppresses numeric gaps for stale data or
uncertain attribution. Identity remains unverified. Unchanged source values
are flagged; source fetch time is not measurement time.

Offline thinning of saved recent samples evaluates target 1/2/3/4/5/10/20/30/60-second
intervals through interpolation error, sample counts and actual gaps. It does
not measure missed handovers or establish OCR accuracy and does not change
frame cadence. See `docs/comparisons.md` for SQL, limitations and integration
checks in an isolated schema. Keep observing over several source updates and
stints before choosing another interval.

The user also requested evaluation below 10 seconds, down to a minimum of one
second on this laptop. The comparison marks unsupported fine intervals as
`finer_reference_required`. `benchmark_frame_intervals.py` times legacy capture
and a persistent 1080p HLS decoder at 1 fps through the full analysis/database
path, saving isolated benchmark observations, timing evidence and a result in
`frame_interval_benchmarks`. Do not confuse HLS media cadence with fresh
wall-clock arrivals or claim this short test establishes sustained reliability.
Regular live polling remains configured separately.

Initial cadence results: all 60/30/12 frames processed at 1/2/5-second media
intervals in separate 60-second runs, with 6/2/0 cycles over each deadline.
Mean analysis+DB times 0.79/0.91/1.04 s; p95 1.55/2.83/1.54 s; max
3.33/4.06/3.64 s. Legacy capture alone took 2.03 and 2.09 s. Five seconds is a
practical tested next candidate, not a long-term reliability guarantee. The
regular app was still at 10 seconds during those initial tests; the later
four-second baseline decision below supersedes that setting. Results/frames live in data/benchmarks/,
PostgreSQL frame_interval_benchmarks, and the copied reports/checksums under
proof-of-concept/test-data/2026-10-25-comparisons/. No test went below 1 s.

## Four-second base and adaptive backpressure (latest scheduling behavior)

The user requested 3/4-second testing and adaptive slowdown when work overruns.
Full-path benchmarks exposed schema/view deadlocks in the old per-cycle DDL.
`ensure_schema()` now commits schema setup before captures and caches it per
connection configuration. Corrected 60-second tests: 3 s target, 20 cycles,
p95 2.99/max 3.15 s, one overrun; 4 s target, 15 cycles, p95 3.24/max 3.62 s,
zero overruns. Both had zero database errors. Preserve pre-fix runs too.

The base `INTERVAL_SECONDS` is now 4. `polling.py` grows the target immediately
by max(1.25*old_budget,1.25*elapsed), rounds up to 0.1 s, caps at
BACKPRESSURE_MAX_INTERVAL_SECONDS=60, and permits 10% recovery only after
BACKPRESSURE_STABLE_SAMPLES=12 cycles each under 70% budget. Failures back off
by at least 50%; saturated cycles leave a full maximum cooldown. Capture,
analysis, commit, cleanup and prior telemetry import are timed. New timing
events queue locally and import next cycle or at graceful shutdown. No backlog
or overlap is created. Local and database state survives restarts; a manually
changed base replaces an old unpressured base but preserves raised backpressure.

`polling_events` stores decision history and links observations.details.polling.
Current effective intervals are in observations.details.polling_interval_seconds
and passed to camera continuity/comparison freshness logic. See
docs/backpressure.md and proof-of-concept/test-data/2026-10-25-backpressure/.

Captured-frame retention is specified in `docs/frame-retention.md`: keep all captures for two hours, then anomalies plus ten captures before/after and a ten-frame lookbehind, persist capture times/protection in `data/frame-retention.sqlite3`, and delete other ordinary captures.

`IMAGE_STORAGE_LIMIT_BYTES=20000000000`: after each supervisor cycle, log ERROR on excess image bytes under `data/`, then delete oldest image files only until within the limit. The cap overrides anomaly protection; DB/metadata and repository regression fixtures are excluded. Events are saved in the retention ledger.

Clothing descriptions: `clothing_descriptions.py` queues a torso crop once per confirmed stint after folding starts; a separate Compose worker uses local Ollama/Moondream and persists the caption, source image, model and retry details in `presenter_clothing_descriptions`. See `docs/clothing-descriptions.md`. `vision_models` is a separate persistent model volume.

Clothing descriptions are stored in Polish (`description_language=pl`). `clothing_descriptions.py` localizes validated English garment phrases with Polish noun/adjective agreement and migrates existing English descriptions on worker startup, preserving originals in response metadata.

## YouTube live chat quota foundation (2026-10-26)

`youtube_quota.py` stores dated official policy snapshots in `youtube_api_policies`,
project budgets in `youtube_quota_projects` and conservative attempted-request
accounting in `youtube_api_requests`; `youtube_daily_usage` is the daily summary.
Pacific midnight defines the quota day. Failed/uncertain requests remain counted.
Authenticated API calls must use `tracked_request()` for local budget enforcement;
video-frame extraction is separate. OAuth/manual delivery are implemented below;
credentials and automatic posting are not configured. See
[setup, limitations and verification](docs/youtube-live-chat.md).
`youtube_quota_integration_check.py` uses isolated projects and makes no Google calls.

Detailed appearance descriptors v2: head-and-upper-body crops, one local structured visual request covering six appearance categories, Polish text and structured `appearance` JSONB (hair, headwear, glasses, facial hair, accessories and clothing). No sex/gender inference. Original inputs/descriptions are archived in `presenter_description_revisions` on portrait upgrades; historical refresh uses the original frame only.

Detailed descriptors use local `llava-phi3:3.8b` (about 2.9 GB weights), with two CPUs and 6 GB memory maximum for the vision container.

### OAuth/manual chat implementation (2026-10-26)

`youtube_auth.py` implements five-minute local browser authorization with Google
Web client JSON, state/PKCE, serialized refresh, private atomic credential files,
and disconnect/revoke. The optional one-shot `youtube-chat` Compose service mounts
ignored `secrets/youtube/`; only connect publishes loopback port 8765. Exact callback:
`http://localhost:8765/oauth/callback`. Use `make youtube-auth` after Cloud setup.
`youtube_chat.py` previews/sends explicit file contents, verifies channel/chat,
records `youtube_chat_deliveries`, links attempts through
`youtube_api_requests.delivery_id`, and reconciles interrupted sends without retry.
`YOUTUBE_CHAT_ENABLED` defaults false. `youtube_commands.py` is an empty handler
registry for future incoming-message commands; it neither reads nor posts live chat.
The old feature plan's OAuth/manual phases are implemented; incoming command reader,
cooldowns and schedules remain future work. No real account/post was used in tests.
See [setup and command extension guide](docs/youtube-setup.md).

The shared `generate_current_message(stream)` in `utilities/current_person_message.py`
now serves clipboard output and YouTube `preview-current` / `send-current` commands.
`make youtube-preview-current` and `make youtube-send-current` expose those commands;
current-data delivery uses source kind `current_person_message`. The generator
keeps freshness/attribution checks; YouTube refuses messages above 200 characters.

First real manual current-person chat post verified on 2026-10-26 using the
connected channel Bartek Dobrowolski-Nowakowski, video `vaiHWiExDXg`.
Delivery `ecc60c61-e206-4396-8300-8e7d2434697d` was sent with a unique request key;
YouTube returned message ID `LCC.EhwKGkNLSEl4WlQtMXBjREZSMktMUWtkeXJzRjl3`.
The message reported 2014 folds, 44.68 folds/minute and 2681 folds/hour projection.
Only that one-shot container enabled sending; `.env` remains disabled. This confirms
manual OAuth posting, not incoming-command reading or scheduled operation.

### Continuous YouTube !ile worker (2026-10-26)

`youtube_bot.py` now runs as the independent persistent `youtube-bot` Compose
service. `youtube_stream.py` uses the official gRPC `streamList` transport and
Docker-generated `youtube_stream.proto` bindings; each connection is quota-accounted.
The bot is explicitly authorized to answer new exact `!ile` messages using the
shared generator's compact format plus the exact Polish ten-minute disclaimer.
It reserves one global cooldown per chat/command and suppresses throttled requests
silently. `youtube_bot_commands` stores enabled/handler/disclaimer/cooldown=600/max-age
settings. `youtube_bot_command_events` stores deduplicated incoming command usage,
source author ID, outcomes and delivery links; `youtube_bot_cooldowns` persists
reservations; `youtube_bot_state` persists activation/cursor/health. Usage views
provide totals and Warsaw daily aggregates, including answered/throttled/stale/
unavailable/failed/uncertain categories. Stats default to the configured stream and
exclude integration streams. Old history, commands over 120 seconds old and stale
measurements do not produce replies; uncertain sends are never blindly retried.
The worker enables replies through its Compose environment override; manual
`.env` sending remains false. Control it with `make youtube-bot-up/stop/stats/logs`.
Docker restart policy is unless-stopped; only an already running bot is recreated
by docker-upgrade. See [bot behavior and schema](docs/youtube-bot.md).

### Automatic final-stint chat summaries

`youtube_stint_notifications.py` runs a 15-second timer inside the bot container,
independent of chat batches. Newly closed presenter sessions (sustained handover
or detected night pause) trigger one compact final-statistics message plus the
existing disclaimer. Final counts/pace come from that specific closed row in
`presenter_session_rates`, never from the current successor. Manual resets, open
stints and closures ending before initial feature activation are ignored.
`youtube_stint_notification_settings` stores stream enable/activation/max-age=3600;
`youtube_stint_notifications` stores UUID-based claims, exact final data/text,
source closure times/reason, delivery and outcome. Unknown outcomes never resend;
recovery reconciles interrupted claims. The source kind is `stint_finished`.
These automatic messages bypass the manual !ile request cooldown and do not inflate
viewer command statistics, but use the same global quota and posting guard.
`make youtube-bot-stats` reports their settings and recent history. Feature tests
use isolated real-PostgreSQL fixtures and simulated sends only.

`!pomoc` is enabled with handler `help`, a separate chat-wide 600-second cooldown
and its own exact Polish cooldown disclaimer. It dynamically lists enabled
implemented commands/descriptions from `youtube_bot_commands.description`, works
without folding data, and shares command-event usage/delivery accounting.
Repeated !pomoc requests do not consume !ile's independent cooldown. See bot docs.

`!samsung` uses the persistent `youtube_bot_command_cache` row to answer in Polish:
Samsung Galaxy Z Flip3, final 418500 folds, still working but unable to close fully.
The supplied Cyfrowa.rp.pl article's updated final result was verified on 2026-10-26;
canonical URL/title, structured facts and summary are cached once without startup
refreshes. No article fetch occurs on command use. Its own cooldown is 600 seconds,
usage/delivery use the common path, and !pomoc includes it dynamically.

## Source naming correction (2026-10-26)

The user clarified that ipduotest-pl is unofficial. Use `ipduotest-pl` for this
source, collector service and archive path, and `ipduotest_pl` for Python/SQL
identifiers. Never describe its values as official or authoritative. Existing
archives and database rows migrate in place; see `docs/ipduotest-pl.md`.

Provider rateLimitExceeded is now governed by a durable account-wide posting pause
in `youtube_account_posting_state`, separate from per-command caps. The shared
transport blocks POST reservations during this pause; command handling records
account_backoff before send, and automatic summaries are recorded as rejected rather than waiting for later delivery.
Startup restores missing pause state from prior successful/rejected POST history.
Default app waits progress roughly 10/20/40/60 minutes with positive jitter and
honor longer Retry-After; these are not guaranteed Google unblock times. New
provider errors are sanitized in youtube_api_error_details and emit ERROR
`youtube_posting_rate_limited` with the retry time. Stats include active posting
backoff. No automatic resend/probe is introduced and stale requests are discarded.

### Reviewed false clothing cues and repair (2026-10-26)

Hands/phone crossing the supplemental chest patch produced 17 false pending cues
at 07:29–07:37 Warsaw time while the user confirmed the presenter did not change.
The old interrupted-cue rule correctly preserved uncertainty but left a sticky
attribution-review flag and froze that stint's last counter. `camera_views.py` now
requires full-patch changes to be corroborated by the upper half and upper-right
anchor; the smaller anchor uses two-thirds of the full-patch threshold. New
baseline features are reconstructed from original evidence, never the current
frame. Original false-alarm JPEGs/checksums are in the clothing-occlusion dataset;
recorded real dark-shirt handover, brief appearances and layout tests remain.
`repair_presenter_review.py` previews/applies an explicitly human-confirmed
same-presenter resolution with stream locking, outbox checks and full pre-change
backups in `presenter_review_resolutions`. It preserves the stint ID and baseline,
replays only accepted counters, annotates historical observations/cues and clears
review/candidate tracking without resetting clothing identity. Generic unresolved
cues are not silently auto-cleared. Message-generation errors now distinguish
review holds, pending changes, unknown cameras and actual night breaks.

Live audited resolution applied at 07:49 Warsaw on 2026-10-26:
`ae8e04dd-5f31-4388-bb18-39a8721692f1`, preserving active stint
`c44a8bda-017f-4d94-9df1-ed978ad32e6d`. Replayed 228 reviewed observations,
resolved 39 brief cues, restored the last counter from 67918 to 69248, and retained
all original records in the audit. Subsequent live observations advanced to 69290
with review=false and no candidate; the original stint remained open. Thirty
relevant tests plus the isolated audit-repair integration check passed.
