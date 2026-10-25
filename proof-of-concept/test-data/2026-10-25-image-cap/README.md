# 20 GB image cap validation

The Compose image passed 88 unit tests, including six quota tests for oldest-first
image deletion, preserving metadata and queues, extensionless archive images,
symlink exclusions, restart rechecking, and ERROR reporting on failed deletions.
Quota tests use small synthetic image files in temporary directories; production
was not deliberately filled to 20 GB. See `compose-tests.txt`.

The quota applies to image files under `data/` and overrides anomaly-window
protection. Database history and non-image metadata are excluded. Policy and
configuration are documented in `docs/frame-retention.md`.
