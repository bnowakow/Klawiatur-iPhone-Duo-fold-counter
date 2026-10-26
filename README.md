# Folding livestream supervisor

For future development sessions, start with [CONTEXT.md](CONTEXT.md): goals,
architecture, decisions, deployment snapshot, known limitations, and handoff steps.

Targets a fresh YouTube live frame every **4 seconds**, with adaptive backpressure, reads the seven-segment
counter, checks its pace, compares clothing colours, and records every attempt in
PostgreSQL. PostgreSQL, the supervisor, and a public-site data collector run in Docker Compose.
Monitoring continues while Docker is running and the host machine is awake.

Configuration lives in the Git-ignored `.env` on the host. On a new checkout,
copy `.env.sample` to `.env` before starting. Compose loads it into the application
environment and mounts the same file read-only at `/app/.env` in both application
containers. The file is excluded from Docker image builds.

```sh
docker compose up -d --build
docker compose logs -f supervisor
```

After changing code, dependencies, or the host `.env`, apply the changes with:

```sh
make docker-upgrade
```

This builds the application images, ensures PostgreSQL is available, and
recreates both application containers with the current configuration. Existing
database and evidence volumes are preserved.

`INTERVAL_SECONDS=4` sets the minimum target interval. Checks run serially.
An overrun immediately increases the next delay with 25% headroom. After 12
consecutive light cycles the delay can decrease by 10%, down to that minimum.
The default maximum is 60 seconds, with a full cooldown if processing exceeds
it. Decisions persist across restarts and are stored in PostgreSQL; no backlog
or overlapping captures are created. See [adaptive polling and measurements](docs/backpressure.md).
Runs start immediately on launch.
`ACTIVE_START=08:00` and `ACTIVE_END=23:00` would capture only during that window
in `TIMEZONE=Europe/Warsaw`. The default equal times mean 24-hour monitoring.
The expected folding pause is approximately **22:00–06:00 Europe/Warsaw**;
capture remains enabled all day because these times are approximate.

## Data collection scope

Presenter appearance, first folding evidence and finish bookmarks are retained
with estimated video offsets, review links and human-review fields. Run
`make docker-video-events`; see [video timestamp evidence](docs/video-events.md)
for timing limits and training-label review.

For now, the goal is storing the inputs for an independent application in
PostgreSQL. Counter history, presenter starts and endpoints, brief appearances,
attribution uncertainty, and ipduotest-pl’s published reference data are
retained. Rankings, presentation and reconciliation policy can be decided later.
See the [database storage contract](docs/data-storage.md) for the field mapping,
retention and remaining measurement limits.

## Git commits

```sh
make codex-commit
```

This stages all non-ignored changes, asks the Codex CLI for a concise message,
and creates a commit. It checks configured upstream updates and offers to push,
preview the diff, or skip pushing. It supports the initial commit and does not
require version bumps. Review your changes before running it.

The helper uses `codex` on PATH or the CLI bundled with Codex/ChatGPT on macOS.
Set `CODEX_BIN=/absolute/path/to/codex` to select another executable. The CLI
must already be authenticated. Optional `dialog`/`whiptail` provide menus;
otherwise it uses plain terminal prompts.

## ipduotest-pl’s published data

ipduotest-pl is an unofficial source. Its published values are archived as source
claims, not treated as authoritative measurements.

The independent `ipduotest-pl` Compose service archives the public data from
[ipduotest-pl](https://ipduotest.pl/) every five minutes: the homepage, full
embedded tables (overall history, current tester score and Top 5), and chart.
Original responses, parsed cells, source metadata and fetch times are retained
in PostgreSQL and `data/ipduotest-pl/`, including revisions and failed attempts.
Start all services with `docker compose up -d --build`.

See [published-data context and operations](docs/ipduotest-pl.md) for the
source inventory, database schema, queries, refresh settings and limitations.
ipduotest-pl hourly slots and scores stay separate from our detected presenter
stints; they have no participant names and do not override OCR validation.

## Current tester comparisons

Generate a ready-to-copy Polish message using our current person's average:

```sh
make docker-message
```

It prints `ilość złożeń przez aktualną osobę …. dotychczasowe tempo składania: … złożeń/min, w tym tempie telefon
powinien być złożony … razy w ciągu godziny` and copies it to the macOS clipboard
using host `pbcopy`. If message generation fails, the clipboard stays unchanged.
Without `pbcopy`, the message is printed for manual copying. The projection is the unrounded
average since the recorded folding start multiplied by 60, rounded to a whole
fold. The fold count is the accepted counter increase since that stint's first
accepted baseline (`last_counter - baseline_counter`), so an initially missed
start can undercount it. The projection is that person's total for one full hour, not the phone's
cumulative counter. The command reads PostgreSQL through the running supervisor
and does not fetch website data or send the message anywhere. Missing, stale,
break or uncertain-stint data produces an explanation instead (exit 2).

`compare_current_tester.py` compares the site's current-tester folds/minute with
our current stint and recent pace. The website collector saves a comparison
after each refresh. PostgreSQL `tester_comparisons` retains source and stint
IDs, both time windows, gaps, freshness/attribution status, and offline
1/2/3/4/5/10/20/30/60-second frame-interval diagnostics. The configured baseline is four seconds, with adaptive backpressure.
See [comparison commands and interpretation](docs/comparisons.md).

## Counter validation

Raw OCR and accepted values are separate database columns. The first reading is
provisional, and a second plausible reading establishes an anchor. Later values
must be nondecreasing and fit a pace envelope around the median of the last 20
accepted positive rates. Before positive rates have been learned, the initial
range is **29.5–56.7 folds/minute** from 27 completed tester rows in the fresh
[ipduotest-pl history](https://docs.google.com/spreadsheets/u/0/d/1GrqBCt6WzAPBa_mLNPHODPIJkem51wLg5cCatnYw9BM/htmlview/sheet?headers=true&gid=0).
`EXPECTED_RATE_MIN=0.4916666666666667` and `EXPECTED_RATE_MAX=0.945` express
these bounds in folds/second and replace the former scalar `EXPECTED_RATE`.
The initial bounds also validate the second provisional/post-gap sample.
Blank, note and future rows are excluded; unchanged counters remain pauses.
Once positive samples are learned, their median replaces the initial range
and `RATE_TOLERANCE=0.5` permits 50% variation around that median. Initially,
tolerance is not applied again to the published endpoints. Both modes add
`COUNTER_SLACK=5` folds and cap increases at `MAX_RATE=5` folds/second.
The source snapshot and derivation are preserved in
`proof-of-concept/test-data/2026-10-25-rate-range/`; this is a fixed initial
calibration, not a dependency on the website at supervisor startup. Updating
the website archive does not automatically change configured bounds.
These parameters are
provisional and configurable in `.env.sample`.

A decrease, excessive jump, slow positive increase, or unreadable digit is logged
with its reason and does not change the trusted anchor or train the pace model.
An unchanged counter is recorded as `accepted_paused`: this can mean a break or
stalled video, not a verified valid increment. Byte-identical frames are logged
as duplicates and do not contribute evidence. After a gap of 600 seconds from
the last accepted reading, two plausible readings are needed to establish a new
anchor; this permits recovery after overnight pauses or persistent OCR issues.
The previous anchor still prevents decreases and physically implausible jumps
across a gap. An actual counter reset needs manual state correction.
Rebased readings are explicitly marked `accepted_rebased` and are less certain
than uninterrupted pace checks. The validator detects suspicious readings;
it cannot prove every accepted OCR result is correct.

## Possible handovers

Only colour and brightness histograms of a clothing-only crop are compared; there is no face
recognition, biometric matching, or identity verification. The detector matches background patches against two calibrated broadcast layouts:
presenter as the main view, or overhead as the main view with the presenter in
the bottom-right inset. It selects a separate clothing-only crop and baseline
for each layout. `CLOTHING_ROI` controls the presenter-main crop; the inset crop
is calibrated in `proof-of-concept/camera-profiles/layouts.json`.

The shifted main-camera framing introduced on 26 October is calibrated separately
as `presenter_main_morning`, with its own background reference and clothing crop.
Original morning transition frames and counter labels are preserved under
`proof-of-concept/test-data/2026-10-26-morning-resumption/`.

Switching views resets pending evidence and never compares clothing across
layouts. A new view creates its own baseline, so a handover during a switch
may be missed. Unrecognized or ambiguous views produce
`camera_view_unknown_review_required`, with no clothing score or baseline
update. New compositions require calibration. Background matching is a
provisional scene check, not a general person or pose detector.

A distance of at least 0.45 sustained for 60 seconds produces
`possible_handover_review_required`. At an uninterrupted four-second cadence this needs 16
changed samples spanning 60 seconds; backpressure can lengthen that cadence. Gaps longer than `max(2.5 * effective polling interval, 25)` seconds
reset pending evidence. An hour passing never implies a handover. Similar
clothing can conceal a handover; lighting or an empty chair
can trigger one. Unconfirmed candidates never replace a baseline. A sustained cue creates a
provisional presenter stint and replaces the clothing baseline once, as
described below.

For a manually verified handover that automatic tracking missed, start a new
stint and reset the clothing baseline on the next frame (do not repeat this
for an automatically recorded stint):

```sh
docker compose stop supervisor
docker compose run --rm supervisor python supervisor.py --once --reset-clothing-baseline
docker compose start supervisor
```

Counter state, clothing baseline histograms, pending evidence and timestamps
are stored in PostgreSQL and survive supervisor restarts. Configuration changes
to clothing profiles, crop, threshold or confirmation duration reset its per-view
baselines on the next observation. Changing only the polling interval preserves them.
The first run after upgrading from the fixed crop also creates fresh baselines.

## Presenter start times and folds per hour

`presenter_recent_pace` reports recent folds/minute over a rolling three-minute
window of accepted readings within the current recorded stint. It uses actual
elapsed timestamps and exposes `measured_seconds`, `sample_count`, and
`measured_at` so short or stale windows are visible. This differs from
`stint_folds_per_minute`, the average since the recorded folding start; a late
detected start still makes that whole-stint estimate uncertain.

```sh
docker compose exec db psql -U folding -d folding -c "
SELECT measured_at AT TIME ZONE 'Europe/Warsaw' AS warsaw_time,
       recent_folds_per_minute, measured_seconds, sample_count,
       stint_folds_per_minute
FROM presenter_recent_pace;"
```

Each inferred presenter stint has a UUID in `presenter_sessions`. These are
anonymous stints, not recognized identities: a returning person gets a new
stint. The detector still uses clothing colours, so similar shirts can hide a
handover and a shirt/lighting change can create a false one. Automatic records
from a sustained cue remain provisional and need human review.

Two start times are stored separately:

- `detected_at`: the first changed sample in a sequence that eventually meets
  `HANDOVER_CONFIRM_SECONDS`. `confirmed_at` is when that sustained signal was
  confirmed. Short changes that disappear, unknown scenes, gaps, and camera
  switches that interrupt confirmation do not create a new stint.
- `folding_started_at`: the first observation with an **accepted counter
  increase compared with an earlier accepted reading within that stint**.
  Samples collected during the confirmation wait are retained, so folding can
  start before `confirmed_at`. The first accepted value establishes
  `baseline_counter` and `baseline_counter_at`; it alone cannot prove an
  increase for the new person. Rejected, provisional, unreadable and duplicate
  readings cannot start folding. `folding_start_counter` and
  `folding_start_observation_id` preserve the first-increase evidence. If no
  increase is observed, the folding start stays NULL.

The first known camera view after installing this feature creates a
`first_observed` stint. Its detection time means monitoring began, not the
person's actual arrival. No historical start time is invented. On later
restarts the current stint and pending candidate reload from PostgreSQL.
`--reset-clothing-baseline` explicitly closes the current stint and starts a
new one with reason `manual_baseline_reset`; use it only after reviewing a
handover. Ordinary camera-profile/config changes do not create a new stint.

While a change is pending, accepted samples belong to its candidate, and the
previous stint's counter endpoint is frozen. If the change is confirmed, the
previous stint gets `ended_at = new detected_at`, the candidate becomes a
stored stint, and the current clothing is adopted as the new baseline. Other
camera baselines are cleared for relearning. This acknowledges the cue once
rather than creating repeated stints from the same shirt. If a candidate
clears, it is preserved as a review event and attribution to the old stint is
held until a sustained cue establishes a new stint or a manual baseline reset
resolves the transition. Candidate evidence is
persisted in `monitor_state` until confirmation; confirmed stints and their
observation evidence are saved together in one transaction. Outbox replay also
restores model state after a mid-cycle database failure and cannot overwrite a
newer stint snapshot with older evidence.

Brief appearances are stored independently in `presenter_appearances`, even
when they last less than a minute or never show an accepted counter increase.
Their statuses distinguish pending, interrupted, brief, and sustained cues.
`details.folding_evidence` is `no_accepted_increase_observed` until an accepted
increase within that appearance is observed; missing or rejected OCR does not
prove that no folding happened. These cues do not establish identity or create
a confirmed folding stint. Start, last, and closing observations are retained,
with images in `data/presenter-review/` outside ordinary capture pruning but
subject to the global 20 GB image cap.
The clothing detector also compares 16-bin brightness histograms (review
threshold 0.60) to distinguish light gray from white fabric. Lighting changes
can trigger this cue; the same persistence and camera continuity rules apply.

`presenter_session_rates` provides two rough wall-clock rates:

- `nominal_folds_per_hour`: `(last_counter - baseline_counter) * 3600 /
  (end_or_last_counter_time - detected_at)`. Closed stints use `ended_at`;
  open stints use their last accepted counter time.
- `folding_folds_per_hour`: `(last_counter - folding_start_counter) * 3600 /
  (last_counter_at - folding_started_at)`. This excludes the increase that
  established the folding start, since its precise start time is unknown.

Both include pauses within their elapsed interval; neither measures active
hand movement. Rates are NULL until sufficient counter/time evidence exists.
The view also exposes the fold deltas and elapsed seconds used. The previous
person's final sample can precede the estimated handover; boundary folds are
therefore approximate and may go unattributed. Start timestamps use frame
receipt time, have roughly the polling interval's resolution, and include
stream latency. New angles can conceal a handover while a baseline is learned;
unknown views suspend stint updates. Increases spanning capture gaps remain
rough estimates. Review these limitations before comparing presenters.

Inspect stints (change the stream URL if configured differently):

```sh
docker compose exec db psql -U folding -d folding -c "
SELECT id, detection_reason,
       detected_at AT TIME ZONE 'Europe/Warsaw' AS detected_warsaw,
       confirmed_at AT TIME ZONE 'Europe/Warsaw' AS confirmed_warsaw,
       folding_started_at AT TIME ZONE 'Europe/Warsaw' AS folding_start_warsaw,
       ended_at AT TIME ZONE 'Europe/Warsaw' AS ended_warsaw,
       baseline_counter, folding_start_counter, last_counter,
       round(nominal_folds_per_hour, 1) AS nominal_folds_per_hour,
       round(folding_folds_per_hour, 1) AS folding_folds_per_hour
FROM presenter_session_rates
WHERE stream_url = 'https://www.youtube.com/watch?v=vaiHWiExDXg'
ORDER BY detected_at;"
```

The schema and rate view are created automatically on upgrade. Existing
observations and counter state are preserved. Deploy the updated code with
`docker compose up -d --build supervisor`.

## Night stop times

The calibrated overnight scene now has its own `night_break_detected` status.
It requires a match to both the actual break board (including selected blue
lettering pixels) and a separate empty-background patch. A blank board,
obstruction, unknown scene or time of day alone is insufficient. This matches
the saved “BREAK UNTIL 6:00 AM (UTC+1)” board; it is not general sign-text OCR.
The profile is `proof-of-concept/camera-profiles/night-break.json`, and immutable
real before/during-break evidence is in `test-data/2026-10-25-night-break/`
under `proof-of-concept/`.

Break scenes never create clothing baselines, appearance candidates or new
presenter stints. Thin LED strokes in this view are decoded using separate
adaptive horizontal/vertical segment thresholds. Three consecutive matching
break-scene counter reads may confirm a small final increment (at most
`COUNTER_SLACK`) as `accepted_break_boundary` without training the pace model.
This narrowly handles a few final folds after a long pause; decreases and
large jumps still fail normal validation. The correction remains unassigned
to a presenter and is retained in end evidence separately from stint totals.

Between `NIGHT_STOP_START=21:30` and `NIGHT_STOP_END=06:30` in `TIMEZONE`,
an open presenter stint closes after `NIGHT_STOP_IDLE_SECONDS=180` seconds
of unchanged accepted counter readings, with at least three samples and no
gap longer than `max(2.5 * effective polling interval, 25)` seconds. These
configurable defaults allow for the approximate 22:00 night break. An active
counter is never closed just because 22:00 arrives. Unreadable readings and
long gaps reset confirmation; exact duplicate frames supply no evidence.

`presenter_sessions.ended_at` stores the estimated stop, backdated to the last
observed increase when available, otherwise the first stationary sample.
`end_reason` is `night_pause_counter_stopped_review_required`.
When the explicit break scene is also matched, the reason is instead
`night_break_sign_counter_stopped_review_required`, and endpoint details include
`break_scene_verified=true` plus the template distances. The last trusted
increase of the preceding stint remains its estimated stop, rather than the
later time at which the improved decoder confirms the final display reading.
`presenter_session_ends` stores the separate confirmation time, observation ID,
counter, sample count, last-increase interval and other supporting evidence.
This is an estimated folding/stint endpoint: presenter disappearance is not
independently verified, and `details.presenter_absence_verified` is false.
It can also trigger if a presenter remains seated while folding has stopped.
During this night window, a sustained clothing-change cue without an accepted
increase within the candidate is retained for review but does not create a new
folding stint. This avoids promoting an empty-chair colour change into a
nonfolding presenter stint while the night-stop evidence accumulates.

The closed stint stays closed while the counter is stationary. Once an
increase is seen outside the break scene, two plausible readings re-establish
the pace anchor using the initial range, even if stationary overnight samples
kept the old anchor fresh. Decreases and absolute-maximum jumps remain rejected.
Once an accepted increase and known camera layout are observed, a new anonymous stint
starts with reason `activity_resumed_after_night_pause`, and clothing baselines
are refreshed. Unresolved appearance review from the closed stint is cleared
for the new stint; any pending appearance record is retained with its resolution.
The next accepted increase within that new stint establishes its folding start.
A returning person is not identified as a previously seen person.
Normal handovers retain their existing `ended_at` behavior.

```sh
docker compose exec db psql -U folding -d folding -c "
SELECT s.id, s.detected_at AT TIME ZONE 'Europe/Warsaw' AS appeared_at,
       s.ended_at AT TIME ZONE 'Europe/Warsaw' AS estimated_stop,
       e.confirmed_at AT TIME ZONE 'Europe/Warsaw' AS stop_confirmed,
       s.last_counter, s.end_reason
FROM presenter_sessions s LEFT JOIN presenter_session_ends e ON e.session_id=s.id
WHERE s.stream_url='https://www.youtube.com/watch?v=vaiHWiExDXg'
ORDER BY s.detected_at DESC LIMIT 20;"
```

Synthetic night-stop integration check:

```sh
docker compose run --rm -v "$PWD/proof-of-concept:/fixtures:ro" supervisor \
  python night_stop_integration_check.py
```

Real-pixel break-transition integration check, including PostgreSQL endpoint
persistence and attribution boundaries:

```sh
docker compose run --rm -v "$PWD/proof-of-concept:/fixtures:ro" supervisor \
  python break_scene_integration_check.py
```

The real break on 2026-10-25 was confirmed at 22:15:15 Warsaw time. Its stint
stop estimate is 22:00:57, the last counter observation attributed to that stint
(63,330); the final visible counter is 63,333. The later decoder-recovery time
was initially used as the stop estimate and was corrected with an explicit
`night_break_endpoint_refined` audit observation. The original confirmation
time and original observation snapshots remain unchanged.

For a similarly affected legacy endpoint, `refine_break_endpoint.py --session
UUID` previews a correction using that session's own last attributed counter
observation. `--apply` writes the correction transactionally, updates the
closed-state timestamp and appends an audit observation. It only accepts a
verified break-sign endpoint with a supporting real counter observation.
It does not identify a presenter, reassign transition folds, or claim an exact
physical stop time.

## Database and evidence

PostgreSQL is available locally at `localhost:54329`, database/user `folding`,
with `POSTGRES_PASSWORD` from `.env` (default `folding-local-dev`). The database
port is bound to localhost. Docker clients use hostname `db` and port 5432.
Postgres data is kept in a named volume. Stop without deleting the data:

```sh
docker compose down
```

Inspect recent observations:

```sh
docker compose exec db psql -U folding -d folding -c "
SELECT observed_at AT TIME ZONE 'Europe/Warsaw' AS warsaw_time,
       raw_counter, accepted_counter, counter_status, counter_reason,
       clothing_status, round(clothing_score::numeric, 3) AS clothing_score
FROM observations ORDER BY observed_at DESC LIMIT 20;"
```

Each `observations` row includes the stream URL, receipt time, raw OCR,
acceptance/rejection, rate, clothing status and distance, frame path, and JSONB
diagnostics including digit boxes and segment occupancies. Capture failures,
duplicates and scheduled inactive checks also get rows. Receipt time is not
the original broadcast timestamp; YouTube delivery includes some latency.

Full frames, counter overlays and clothing crops live in
`data/frames/<observation-id>/` on the host (`/data/frames/…` inside Docker).
Unknown camera views are additionally copied with their observation metadata to
`data/camera-review/<observation-id>/`, outside ordinary capture pruning.
All images under `data/`, including review archives and website image blobs,
are subject to the global 20 GB image cap; numeric and metadata history remains.
See [frame retention](docs/frame-retention.md) for anomaly windows and quota eviction.
Keep this archive for calibrating future angles. Preserved test datasets and
checksums live in `proof-of-concept/test-data/`; see its README for the procedure
to add angles while retaining all previous regression evidence. Database
outages are queued as local JSON observations in `data/outbox/` and replayed
idempotently on recovery. No capture starts without a database connection.
If a database failure occurs after completed analysis, its observation and
model state are queued together and restored during
replay before the next analysis. A PostgreSQL transaction lock prevents
two supervisors analyzing the same stream simultaneously. Avoid multiple
streams sharing the same `data/outbox` directory.

## Validation

```sh
docker compose run --rm supervisor python -m unittest discover -s proof-of-concept -p 'test_*.py'
```

Saved-image integration check using a separate stream key:

```sh
docker compose run --rm -e STREAM_URL=fixture-test \
  -v "$PWD/proof-of-concept:/fixtures:ro" supervisor \
  python supervisor.py --once --fixture /fixtures/live-frame.jpg
```

Full PostgreSQL integration check (isolated stream key, no live capture):

```sh
docker compose run --rm -v "$PWD/proof-of-concept:/fixtures:ro" supervisor \
  python integration_check.py
```

Presenter-session database integration check:

```sh
docker compose run --rm -v "$PWD/proof-of-concept:/fixtures:ro" supervisor \
  python session_integration_check.py
```

Its fixtures, observation log and result are preserved in
`data/test-evidence/presenter-sessions/<test-run-id>/`. Set
`SESSION_TEST_OUTPUT_DIR` on the test container to choose another archive path.

This also verifies both start timestamps, candidate evidence, stint closure,
SQL hourly rates, transaction rollback, and idempotent outbox recovery.

The original integration check checks actual inserts, model reloads, spike rejection, and durable replay
after a simulated database outage. Test rows use an `integration:` stream key.

Tests cover million-scale and variable-length OCR, steady pace, spikes,
decreases, slow readings, pauses, long gaps, clothing persistence at multiple
intervals, camera layout switches, unknown scenes, presenter start times,
stint transitions, and active-hour windows. Real handover accuracy still requires
human verification against an actual presenter change.

Implementation references: [Psycopg parameter binding](https://www.psycopg.org/psycopg3/docs/basic/params.html)
and [Compose database readiness](https://docs.docker.com/compose/how-tos/startup-order/).

Captured images follow [anomaly-window retention](docs/frame-retention.md); database history remains.

Licensed under the GNU General Public License v3.0; see [LICENSE](LICENSE).

Each confirmed folding stint receives a Polish appearance description (visible clothing, hair, headwear, glasses, facial hair and accessories) from a local background vision worker, stored in PostgreSQL. See [clothing descriptions](docs/clothing-descriptions.md).

YouTube live-chat API policies and conservative PostgreSQL usage accounting are
available through `youtube_quota.py`; see [YouTube quotas and OAuth setup](docs/youtube-live-chat.md).
Google OAuth and manual delivery are available; scheduled broadcasts remain disabled.

Google OAuth and explicit manual YouTube chat delivery are implemented. Follow the
[Google Cloud setup and chat commands](docs/youtube-setup.md). Sending defaults off;
an empty command-handler registry supports future bot extensions.

The [continuous YouTube command bot](docs/youtube-bot.md) answers `!ile` with current
folding statistics and a disclaimer, at most once per ten minutes across the chat.
Its command settings, cooldowns and usage statistics persist in PostgreSQL.
Use `make youtube-bot-stats`, `make youtube-bot-logs` and `make youtube-bot-stop`.

The bot also posts one final-statistics summary when a newly observed folding stint
ends through a handover or night stop. Automatic summaries use the completed
person's data, persist delivery history, and do not replay historical stints.
