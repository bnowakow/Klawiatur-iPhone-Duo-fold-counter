# Database storage contract

The current scope is collecting and retaining the data needed for an independent
folding-test application. Presentation, rankings policy, participant naming and
reconciliation with the organizer's website can be decided later. The source of
our measurements is the livestream; the website archive is a separate reference
history and does not control our counter or presenter attribution.

## Data retained in PostgreSQL

| Future use | Stored inputs | Database location |
| --- | --- | --- |
| Cumulative counter history and time-series charts | Stream URL, observation UUID, UTC receipt timestamp, raw OCR value, accepted value, verdict, reason, pace and OCR diagnostics | `observations`, including `details.ocr` and `details.counter_validation` |
| Presenter arrival and actual folding-start estimates | Stint UUID, first detection, sustained-cue confirmation, first accepted increase time and counter, evidence observation IDs | `presenter_sessions` |
| Individual fold totals and duration | Counter baseline and timestamp, first-increase counter, last accepted counter and timestamp, stint end time and reason | `presenter_sessions`; intermediate evidence remains in `observations` |
| Night-pause stop estimates and confirmation | Estimated stop, separate confirmation time, counter, sample count, first stationary observation, last-increase interval, and explicit unverified-absence flag | `presenter_sessions.ended_at`, `.end_reason`; `presenter_session_ends`; immutable snapshots in `observations.details.presenter_session_updates` |
| Hourly/minute rates and future rankings | The above counts, endpoints and timestamps; rolling accepted samples | `presenter_session_rates`, `presenter_recent_pace` and their underlying tables |
| Brief appearances without established folding | First/last observation evidence, raw and accepted readings, appearance status, first-increase evidence when present | `presenter_appearances.details`; `observations.details.presenter_appearance_updates` |
| Attribution uncertainty | Pending candidate IDs, camera selection, unknown views, interrupted changes, attribution-review flag and clothing scores | `observations.details`; latest working state in `monitor_state` |
| Capture gaps, pauses, failed OCR and duplicates | Every recorded attempt's status and diagnostics, including accepted unchanged counters | `observations` |
| Organizer's complete published history and schedule | Every fetched sheet row, future slot, blank field, original localized value, and source configuration | `official_data_resources.parsed` and `.metadata`, plus original HTML in `official_data_blobs.body` |
| Organizer's current score, estimated final score, measurement minute and Top 5 | Complete published source tables with fetch timestamps | `official_data_resources`; source responses in `official_data_blobs` |
| Published test-event notes and chart history | Sheet note rows such as day closure and dust test; original chart images, homepage and subsequent snapshots | `official_data_resources` and `official_data_blobs` |
| Adaptive scheduling | Per-cycle processing time, previous/next delay, stability streak, outcomes and restart state | `polling_events`; local state/outage queue in `data/polling/` |
| Pace comparison and frame-cadence evaluation | Source run and stint IDs, rates/gaps, windows, freshness and attribution status, thinning diagnostics; live interval benchmark timings | `tester_comparisons`, `frame_interval_benchmarks` |
| Provenance, source changes and collection failures | Run UUID, start/end times, URL, fetch status, response headers, SHA-256 and errors | `official_data_runs`, `official_data_resources`, `official_data_blobs` |

`presenter_session_rates` and `presenter_recent_pace` are views. The underlying
counter values and timestamps are stored, so rates and rankings can be rebuilt
under a different policy later. Individual totals are estimates between sampled
boundaries; uncertain observations are not proof of who performed a fold.

Appearance and stint UUIDs do not identify a person by name. A returning person
may have multiple stints. A brief appearance without an accepted increase is
retained as evidence, not classified as a person who performed zero folds.
Samples marked for attribution review remain available for later decisions.

The calibrated night-break scene is logged in
`observations.clothing_status='night_break_detected'` and `details.camera_view`.
Its matching diagnostics and explicit `break_scene_verified` flag accompany
the `presenter_session_ends` endpoint. The final display reading may include
small transition increments that are retained as counter history and endpoint
evidence without increasing the preceding stint's attributed `last_counter`.

The organizer's forecasts and displayed rates are retained exactly as published.
Our own forecasts do not yet have an agreed formula; the measurement history
needed to calculate them later is retained. No future slot or planned duration
is treated as an observed event. The current system does not independently
recognize dust tests or other physical interventions from video; published
notes are archived separately and can support later event annotation. No
unknown event time, participant identity or historical arrival is fabricated.

## Retention and recovery

Database history has no automatic expiry. Session/appearance tables retain the
latest record for each UUID; their observation update snapshots remain in
`observations.details` for reconstruction. `monitor_state` is the resumable
working model, not the only copy of historical evidence. Published snapshots
are append-only by run/resource key and exact response bodies are deduplicated
by content hash. Successful data is not overwritten by a subsequent failed
fetch; latest-attempt and last-success views expose both states.

A supervisor observation completed during a database outage is queued in
`data/outbox/` with its model state for transactional replay. Website snapshots
queue independently under `data/official/pending/`. Pending files are not yet
in PostgreSQL until recovery; database failures must remain visible in logs.

Stream images remain files referenced by observation paths. Only detected
anomalies and the ten captured frames before/after each are preserved subject to the global 20 GB image cap;
ordinary frames are deleted after leaving the rolling ten-frame buffer. See
[anomaly-window retention](frame-retention.md). Unknown-camera and presenter-review evidence,
website raw blobs, and regression datasets are retained separately. The database
retains numeric readings, diagnostics, source bytes and timing evidence after
ordinary frame expiry. It does **not** contain all livestream video or every
captured image. Future video reanalysis requires the preserved image/video
files as well as PostgreSQL.

Back up the `postgres_data` volume together with `data/` and the repository's
regression datasets. Named-volume persistence survives routine Compose
restarts; deleting the volume or host without a backup does not.

## Source and operating references

- [Presenter timing, counter validation, rates and SQL](../README.md).
- [Published source inventory, field meanings and archive queries](official-data.md).
- [Preserved regression evidence](../proof-of-concept/test-data/README.md).

The supervisor's configured cadence currently defaults to four seconds, with adaptive backpressure; the
website collector waits five minutes between completed runs. These are polling
targets, not guaranteed source-time precision. Capture latency, missed slots,
site caching, unknown layouts and attribution review remain explicit limits.

All image files under `data/` share the 20 GB cap documented in
[frame retention](frame-retention.md#global-image-limit). Above it, an ERROR is
logged and the oldest image files are deleted; database and metadata are retained.
