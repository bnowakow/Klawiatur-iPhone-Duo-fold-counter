# Presenter-session regression evidence

Synthetic counter values and times on saved camera images; these records do not
represent real presenter changes. Keep this dataset unchanged.

- `unit-tests.txt`: 25 passing unit/regression tests, including a synthetic shirt
  change on real camera frames. The same 25 tests also passed in Compose.
- `counter-integration.jsonl`: existing PostgreSQL counter integration, passed.
- UUID subdirectory: passing presenter-session database integration, source
  frames, annotated evidence, observation log and result. It verifies starts,
  closure, hourly rates, transaction rollback and stale replay.

Observation paths refer to the original temporary test execution directory;
corresponding evidence is preserved here under the matching relative path.
The test run's stream key in result.json isolates database rows from live data.
