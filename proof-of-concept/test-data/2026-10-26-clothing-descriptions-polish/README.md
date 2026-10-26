# Polish clothing descriptions — 2026-10-26

103 unit tests passed. The PostgreSQL integration check verifies Polish text,
`description_language=pl`, preserving the original English description, idempotent
conversion, and existing queue/retry/replay behavior. The old worker was stopped
before deploying the localized implementation. Live migration evidence is stored
in `live-descriptions.json` after verification.

The tested English vision prompt remains unchanged. The user-facing description
is localized deterministically, preserving garment colors, grammatical gender,
sleeve lengths and patterns. Raw model responses remain in diagnostic metadata.
