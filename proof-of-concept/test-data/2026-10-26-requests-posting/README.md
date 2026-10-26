# Requests posting transport — 26 October 2026

User-confirmed diagnostic message was posted once at 09:08:28 Warsaw time.
The screenshot confirms its appearance; a read-only API lookup matched its
text, author and timestamp. Full diagnostic records are in the sibling
2026-10-26-youtube-posting-investigation directory and PostgreSQL.

Production POSTs now use the same requests.Session transport with no retries
or redirects, plus explicit decompression and bounded response reads.
Shared accounting, expiry, persistent cooldowns and idempotency remain.
Tests make no public Google POSTs; network tests use localhost, database tests
use simulated Google results. No additional real test message was sent during
implementation/deployment. Existing cooldown is preserved.

The successful Requests test does not establish why earlier urllib calls failed.
