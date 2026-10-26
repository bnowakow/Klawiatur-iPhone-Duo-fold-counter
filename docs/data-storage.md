# Database storage contract

The current scope is collecting and retaining the data needed for an independent
folding-test application. Presentation, rankings policy, participant naming and
reconciliation with ipduotest-pl’s website can be decided later. The source of
our measurements is the livestream; the website archive is a separate reference
history and does not control our counter or presenter attribution.

## Data retained in PostgreSQL

| Future use | Stored inputs | Database location |
| --- | --- | --- |
| Cumulative counter history and time-series charts | Stream URL, observation UUID, UTC receipt timestamp, raw OCR value, accepted value, verdict, reason, pace and OCR diagnostics | `observations`, including `details.ocr` and `details.counter_validation` |
| Visible appearance per folding stint | Polish clothing, hair, headwear, glasses, facial-hair/accessory description; structured attributes, portrait crop, prior revisions and source/model provenance | `presenter_clothing_descriptions`; [details](clothing-descriptions.md) |
| Presenter arrival and actual folding-start estimates | Stint UUID, first detection, sustained-cue confirmation, first accepted increase time and counter, evidence observation IDs | `presenter_sessions` |
| Individual fold totals and duration | Counter baseline and timestamp, first-increase counter, last accepted counter and timestamp, stint end time and reason | `presenter_sessions`; intermediate evidence remains in `observations` |
| Night-pause stop estimates and confirmation | Estimated stop, separate confirmation time, counter, sample count, first stationary observation, last-increase interval, and explicit unverified-absence flag | `presenter_sessions.ended_at`, `.end_reason`; `presenter_session_ends`; immutable snapshots in `observations.details.presenter_session_updates` |
| Hourly/minute rates and future rankings | The above counts, endpoints and timestamps; rolling accepted samples | `presenter_session_rates`, `presenter_recent_pace` and their underlying tables |
| Brief appearances without established folding | First/last observation evidence, raw and accepted readings, appearance status, first-increase evidence when present | `presenter_appearances.details`; `observations.details.presenter_appearance_updates` |
| Attribution uncertainty | Pending candidate IDs, camera selection, unknown views, interrupted changes, attribution-review flag and clothing scores | `observations.details`; latest working state in `monitor_state` |
| Video review and future training examples | Appearance/start/finish bookmarks, estimated video offsets, replay links, source and confirmation observations, preceding frames/counters and separate human annotations | `presenter_video_events`; `observations.details.capture.video_timing`; [timing limits](video-events.md) |
| Reviewed missed-handover corrections | Boundary observations, unassigned transition folds, original observation/comparison/state snapshots and old/new stint UUIDs | `presenter_handover_corrections`; correction UUIDs in reassigned observation details; superseded comparisons have null application rates |
| Capture gaps, pauses, failed OCR and duplicates | Every recorded attempt's status and diagnostics, including accepted unchanged counters | `observations` |
| ipduotest-pl’s complete published history and schedule | Every fetched sheet row, future slot, blank field, original localized value, and source configuration | `ipduotest_pl_resources.parsed` and `.metadata`, plus original HTML in `ipduotest_pl_blobs.body` |
| ipduotest-pl’s current score, estimated final score, measurement minute and Top 5 | Complete published source tables with fetch timestamps | `ipduotest_pl_resources`; source responses in `ipduotest_pl_blobs` |
| Published test-event notes and chart history | Sheet note rows such as day closure and dust test; original chart images, homepage and subsequent snapshots | `ipduotest_pl_resources` and `ipduotest_pl_blobs` |
| Adaptive scheduling | Per-cycle processing time, previous/next delay, stability streak, outcomes and restart state | `polling_events`; local state/outage queue in `data/polling/` |
| Pace comparison and frame-cadence evaluation | Source run and stint IDs, rates/gaps, windows, freshness and attribution status, thinning diagnostics; live interval benchmark timings | `tester_comparisons`, `frame_interval_benchmarks` |
| Provenance, source changes and collection failures | Run UUID, start/end times, URL, fetch status, response headers, SHA-256 and errors | `ipduotest_pl_runs`, `ipduotest_pl_resources`, `ipduotest_pl_blobs` |

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

ipduotest-pl’s forecasts and displayed rates are retained exactly as published.
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
queue independently under `data/ipduotest-pl/pending/`. Pending files are not yet
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
- [Published source inventory, field meanings and archive queries](ipduotest-pl.md).
- [Preserved regression evidence](../proof-of-concept/test-data/README.md).

The supervisor's configured cadence currently defaults to four seconds, with adaptive backpressure; the
website collector waits five minutes between completed runs. These are polling
targets, not guaranteed source-time precision. Capture latency, missed slots,
site caching, unknown layouts and attribution review remain explicit limits.

All image files under `data/` share the 20 GB cap documented in
[frame retention](frame-retention.md#global-image-limit). Above it, an ERROR is
logged and the oldest image files are deleted; database and metadata are retained.

## YouTube OAuth and live-chat delivery

`youtube_api_policies` retains dated quota/limit snapshots;
`youtube_quota_projects` holds configured local project budgets;
`youtube_api_requests` records conservative attempted-request costs and outcomes;
`youtube_daily_usage` summarizes each Pacific quota day. Nullable
`youtube_api_requests.delivery_id` links POST attempts to
`youtube_chat_deliveries`, which retains request keys, exact public message text,
posting channel/video/chat, source kind/source ID, status and returned message ID.
Delivery and quota history have no automatic expiry. Unknown delivery outcomes
are preserved and never automatically resent. Integration fixtures use isolated
`integration:chat:` / `integration:youtube:` projects.

OAuth client JSON and refresh credentials are private files in ignored
`secrets/youtube/`, mounted only into the optional chat service. They are outside
PostgreSQL and image retention. Protect any credential backups separately; database
or log backups must not acquire tokens. The bot retains command events and resume
state rather than an archive of all chat text. See
[Google setup, delivery and command extension](youtube-setup.md).

### Live-chat command statistics

The persistent bot is now implemented. `youtube_bot_commands` stores `!ile`, its
600-second cooldown and exact disclaimer. `youtube_bot_cooldowns` holds shared
per-chat reservations; `youtube_bot_state` stores activation, cursor and health.
`youtube_bot_command_events` retains unique command message IDs, author channel
IDs, times, outcome and delivery reference. Total and Warsaw-day usage views are
`youtube_bot_command_usage` and `youtube_bot_command_daily_usage`; counts distinguish
answered, throttled, stale, unavailable and failed/uncertain commands. Other chat
content is not archived. History has no automatic expiry. See [bot operation and
queries](youtube-bot.md).

Automatic final-stint messages are tracked separately in
`youtube_stint_notifications`, with final counts/pace, closure timing/reason,
exact message, durable session key and delivery status. Their per-stream
activation, enable switch and maximum age are in
`youtube_stint_notification_settings`. They do not increment viewer command usage.
See [automatic final-stint summaries](youtube-bot.md#automatic-end-of-stint-summaries).

`youtube_bot_command_cache` retains the `!samsung` historical summary, canonical
source URL/title, structured model/fold-count facts, verification date and cache
timestamp. Runtime replies read this snapshot without refetching the source.

Posting rate-limit incidents, sanitized provider errors, account pause state,
backoff decisions and delivery expiry times are retained in PostgreSQL with no
automatic expiry. Suppressed or stale replies remain audit records and are never
queued for later posting. See [posting limits](youtube-rate-limits.md) for tables,
the investigated incident and freshness policy.
