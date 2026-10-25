# Captured-frame retention

Only detected anomalies and the **10 captured frames before and 10 captured
frames after each anomaly** are retained, subject to the 20 GB image limit below. The anomaly itself makes
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
buffer. Ordinary older captures, including their annotated images and clothing
crops, are deleted each cycle. After an anomaly the next ten available captures
are protected even across restarts. Incomplete windows at startup/shutdown contain
only the context actually captured. No missing frames are synthesized.

`data/frame-retention.sqlite3` is a durable local retention ledger, separate from
PostgreSQL measurement history. It records capture order, stream, anomaly reasons
and anomaly-window protection. Keep it with `data/` in backups. Protection commits before
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

At the measured ~463 KB per capture an isolated 21-frame window costs about
9.7 MB. The ordinary ten-frame buffer costs about 4.6 MB per active stream.
Disk growth now depends on anomaly frequency, overlap, and testing, rather than
retaining every routine image. Database/site archives continue growing separately.

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
windows and their lookbehind buffer; old windows can become incomplete. Only image
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
