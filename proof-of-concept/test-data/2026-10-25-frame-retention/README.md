# Anomaly-window retention validation — 2026-10-25

The Compose image passed 75 unit tests, including nine retention cases covering
exact ten-frame boundaries, overlap, retries, independent streams, backfill,
restart persistence and post-insert outage promotion. See `compose-tests.txt`.
The database integration check validates session history and outage replay;
its result is recorded in `session-integration.txt`.

`before.json` and `after.json` record the production capture-tree inventory
around deployment. Startup classified 612 observation records and removed 166
ordinary capture folders. Concurrent live captures explain count differences.
There were no unclassified capture folders after migration. The existing
benchmark and regression data were excluded from cleanup.

The policy is documented in `docs/frame-retention.md` at repository root.
