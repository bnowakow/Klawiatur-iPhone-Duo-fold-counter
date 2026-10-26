# Captured-frame retention

Every captured frame (including crops and annotated images) is retained for a
rolling **two hours**, using the observation capture time. Once older than two
hours, only detected anomalies and the **10 captured frames before and 10 captured
frames after each anomaly** are retained, plus the rolling ten-frame lookbehind.
All retention is subject to the 20 GB image limit below. The anomaly itself makes
21 captures for an isolated event, when all context is available. Overlapping
windows share the existing files. Counts refer to captures, including duplicate
frames, not seconds or individual folds; backpressure changes their time span.
Failed attempts without an image do not consume a frame slot.

Anomalies currently include:

- Counter readings classified as rejected, suspect slow or unreadable.
- Duplicate frames (possible stalled stream).
- Pending clothing changes and any camera/clothing status requiring review.
- Presenter appearance events or attribution requiring review.
- Capture/analysis errors with image files and database errors after capture.

Normal pauses, accepted/rebased readings, provisional initial counter readings
and initial clothing baselines do not alone trigger retention. Repeated anomalies
extend retention; a continuously unknown camera or stalled stream can preserve
all captures until the issue clears.

The newest ten captures per stream remain temporarily on disk as the lookbehind
buffer even if captures are more than two hours apart. Ordinary captures older
than two hours and outside that buffer, including annotated images and clothing
crops, are deleted each cycle. After an anomaly the next ten available captures
are protected even across restarts. Incomplete windows at startup/shutdown contain
only the context actually captured. No missing frames are synthesized.

`data/frame-retention.sqlite3` is a durable local retention ledger, separate from
PostgreSQL measurement history. It records capture order, stream, anomaly reasons
capture timestamp and anomaly-window protection. Keep it with `data/` in backups. Protection commits before
deletion; a failed cleanup can be retried. PostgreSQL observation diagnostics
include `details.frame_retention` with the policy and anomaly reasons. An ordinary
frame may subsequently become protected by a later anomaly. A `frame_path` in
PostgreSQL is a historical reference and does not guarantee the file still exists.
Numeric observations, session times and all other database history remain stored.

At supervisor startup, existing captures are classified from PostgreSQL and the
outbox in timestamp order before cleanup. Unclassified folders remain untouched
if the history is unavailable; restart after recovery to retry their migration.
Existing camera/presenter review archives, benchmarks and explicit regression/test
evidence are outside ordinary capture pruning. Image files under `data/` are
still subject to the global image limit. Repository regression fixtures are excluded.
No seven-day expiry applies to anomaly evidence. `RETENTION_DAYS` is retired.

A sample of the latest 500 retained capture folders on 26 October 2026 averaged
393 KB per capture (95th percentile 639 KB), including derived images. At that
mean size the two-hour debug window uses approximately:

| Capture interval | Captures / two hours | Debug window |
| --- | ---: | ---: |
| 1 second | 7,200 | 2.83 GB |
| 2 seconds | 3,600 | 1.41 GB |
| 4 seconds | 1,800 | 0.71 GB |
| 5 seconds | 1,440 | 0.57 GB |

At the sample's 95th-percentile capture size, even one capture/second is about
4.60 GB per two hours. Actual backpressure reduces capture counts. Total images
at the measurement were 1.63 GB. The existing **20 GB cap remains appropriate**
for this single stream; leave it unchanged. Sizes and other image archives may
grow, so the cap is still enforced. Two hours is a minimum age for ordinary
capture pruning, not a guarantee against cap eviction. Anomalies already inside
the two-hour window are counted once, not duplicated. Older anomaly evidence
continues accumulating until the cap evicts it. Database/site archives grow
separately.

The ledger schema migrates existing rows in place. Startup classification uses
PostgreSQL/outbox observation times for existing files; remaining legacy rows
without times fall back to the frame file's modification time. Restart does not
reset capture age. Cleanup cannot restore files already deleted by the previous
policy: the complete two-hour window fills as new captures arrive.

## Global image limit

`IMAGE_STORAGE_LIMIT_BYTES=20000000000` sets a **20 GB decimal** limit on the
combined logical size of image files anywhere under `DATA_DIR` (normally `data/`).
This includes ordinary/anomaly frames, camera/presenter review images, benchmark
and test-evidence images, and extensionless website image blobs recognized by
image signatures. Repository fixtures in `proof-of-concept/test-data/` are outside
this limit, as are PostgreSQL files, JSON/HTML metadata, queues, logs and the
retention ledger. Symlinks are not followed.

After each supervisor cycle, including inactive/error cycles, the app checks the
image total. If it exceeds the limit, it first logs an ERROR event named
`image_storage_limit_exceeded`, then deletes the oldest image files by modification
time until the total is at or below the limit. The cap takes priority over anomaly
windows, the two-hour debug history and their lookbehind buffer; old windows can become incomplete. Only image
files are deleted by the quota cleaner; directories and their metadata remain.
Normal capture pruning continues to remove obsolete ordinary capture folders.

An ERROR summary `image_storage_trimmed` records before/after usage and deletion
count. The ledger's `image_storage_events` table also preserves the summary and
relative paths of deleted images. Failed deletions are recorded; if the limit
cannot be reached, `image_storage_limit_unmet` is logged at ERROR level and the
next cycle retries. Cleanup survives restarts because usage is measured afresh.
This is a periodic cleanup limit: captures or concurrent archive writers may
briefly exceed it until the next check. Files being rewritten are deferred to the
next scan. Database history and website response bytes stored in PostgreSQL remain
available after image-file eviction; historical file paths can point to deleted
images. This cap is not a limit on overall application disk usage.
