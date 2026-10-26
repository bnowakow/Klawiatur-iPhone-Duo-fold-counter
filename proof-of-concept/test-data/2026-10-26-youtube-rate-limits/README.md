# Retained posting-limit investigation and tests

Collected 2026-10-26. `incident.json` is the PostgreSQL incident export,
including known historical measurements, provider-diagnostic gaps and the policy.
`store_incident.py` is an idempotent database-only import; it never calls Google.
Integration logs use real PostgreSQL and simulated API responses in isolated
fixture projects/channels. Test fixtures and database audit rows are retained.
The unit test log covers the full suite. No test posts real public messages.

The original event 23 remains rejected with exactly one POST attempt.
The deployed worker is healthy. Documentation: `docs/youtube-rate-limits.md`.
