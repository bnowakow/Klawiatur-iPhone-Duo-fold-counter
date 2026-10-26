# Clothing-description validation — 2026-10-26

The final Compose suite passed 101 unit tests. PostgreSQL integration checks
verify per-session uniqueness, replay safety, retry and eventual completion;
existing session timing/outage replay checks also passed.

Actual local-model validation is retained under
`data/test-evidence/clothing-descriptions/vision-fixtures/`. Main and inset white
shirt views, gray and olive changed clothing, and the final garment-only output
were evaluated. The final real generation returns `Grey sweater.` in about 23
seconds; the raw model response is retained alongside the garment-only text.
`model-selection.json` records exploratory models and the initial wrongly labelled
camera view in the validation script. Production uses the detected camera view.

See `docs/clothing-descriptions.md` for database fields, SQL, runtime resources
and retry policy. The worker processes only its configured stream, excluding
integration stream jobs from normal inference.
