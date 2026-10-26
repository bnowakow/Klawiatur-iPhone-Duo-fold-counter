# Published test data from ipduotest-pl

Source: [ipduotest-pl](https://ipduotest.pl/), inspected on 2026-10-25.

ipduotest-pl is unofficial. This archive preserves its published claims and does
not establish their accuracy or authority.
The site describes KlawiterMedia's manual iPhone Duo folding test: participants
usually take hourly turns, and the stated target is device failure or one
million folds. The organizers say they bought the device and Apple did not
provide or sponsor it. The homepage links the same YouTube livestream monitored
by this repository. These are the organizers' descriptions, not independently
verified device or sponsorship claims.

## What is available

The homepage uses Sheetable to load publicly readable Google Sheets HTML views.
The collector follows the same URLs used by the site's frontend. It fetches the
full referenced sheet, before frontend pagination or display limits. It does
not require Google credentials or modify the source sheets.

| Homepage section | Source | Published fields |
| --- | --- | --- |
| Aktualny Wynik Składającego | [Current tester](https://docs.google.com/spreadsheets/u/0/d/1fK3Qn5pPK0sEZGayoWihVKLhizaGhnYR4iplJ7MCd_s/htmlview/sheet?headers=true&gid=225705126), table 4 | Current individual's score, measurement minute, projected final score, folds/minute |
| Top 5 | [Top 5 sheet](https://docs.google.com/spreadsheets/u/0/d/1Gxr4FOcSXjy24hKpS0UbTiVeAKCNrlvZy6PVE7OjW2M/htmlview/sheet?headers=true&gid=950352229), table 3 | Rank, start date/time, fold count, folds/minute; also a trailing leader-gap row |
| Ranking Ogólny | [Overall history](https://docs.google.com/spreadsheets/u/0/d/1GrqBCt6WzAPBa_mLNPHODPIJkem51wLg5cCatnYw9BM/htmlview/sheet?headers=true&gid=0), table 2 | Tester start date/time, cumulative end counter, individual folds, folding minutes, folds/minute; future slots and event notes |
| WYKRESY | [Published chart](https://ipduotest.pl/wp-content/uploads/2026/10/wykres_v2.png) | Original PNG chart, preserved as bytes |

The configuration also lists table 1, titled Harmonogram, pointing to the same
sheet and tab as table 2. Preserve both configurations, but fetch this shared
source only once. At initial inspection there were **three unique sheet views**,
one chart and one homepage: five collected resources per successful run.
Current sources are discovered again on every fetch, rather than fixing the
three spreadsheet IDs in collector code. New Sheetable table entries and
same-site homepage images are included automatically. A different publishing
mechanism requires a collector/parser update.

The initial history archive has 42 rows after the header, including empty rows,
31 nonempty rows, completed stints, future entries and event notes. Notes include
day closure and a dust-contamination test. The source contains Polish decimal
commas. Empty fields stay empty; a displayed `0,0` is retained as text and is not
proof that a future stint occurred. Source table configuration, original HTML,
cell links/images and merge spans are retained along with parsed cell text.
Google row-number and column-letter display chrome is excluded from parsed
cells; it remains in the raw HTML.

## Relation to our measurements

These tables contain start-time labels, not participant names or identifiers.
They cannot establish the identity of an anonymous `presenter_sessions` record.
Keep ipduotest-pl values alongside OCR and appearance-based estimates; collection
does not alter accepted counters, presenter boundaries, or learned pace training.
At the user's request, a preserved 2026-10-25 snapshot of completed positive
`Złożenia na minutę` entries seeds the initial validator range: 29.5–56.7/min.
This fixed configuration calibration is described in the main README; later
site imports do not automatically change it.
Any future match to a presenter stint needs explicit timing evidence or manual
review. An hourly slot in the sheet is not a detected handover.

ipduotest-pl’s `Data / Start Testera` start is distinct from our `detected_at`
and `folding_started_at`. The site does not label a timezone on these sheet
cells; the collector retains their original strings without assigning one.
The `fetched_at` timestamp is our UTC receipt time, not the source's measurement
or edit time. Different tables are fetched separately, so a snapshot is not an
atomic workbook transaction. Source edits and caching may cause disagreement.

In the initial archive, some individual scores differ from the change between
adjacent cumulative totals. For example, 45,000 to 47,863 is 2,863, while the
corresponding published individual score is 2,811. Both are retained unchanged.
Similarly, current-score projections and published minute rates should not be
treated as measured cumulative counters. These differences need review before
using source values to validate our stint estimates.

The chart labels blue bars as normal work and red bars as work after the dust
test. Its initial image ends at the 25 October 18:00 interval, while the
tables already contain later entries, so the image can lag the tables.
The chart is available only as a PNG on the homepage. Its underlying numeric
series is not exposed there; the archive retains the image without inventing
chart points. Private data, unreferenced workbook tabs, pre-collection revisions,
and changes between polling instants are outside this archive's coverage.

## Collection, storage and recovery

The `ipduotest-pl` Compose service runs `ipduotest_pl.py` independently of
the livestream supervisor, immediately at startup and then waits
`IPDUOTEST_PL_INTERVAL_SECONDS=300` after each completed run. Minimum interval
is 60 seconds. It collects during all hours, independent of the stream's active
window. Requests have a 25-second socket timeout and a 20 MiB response limit;
up to four discovered resources are fetched in parallel. Failed fetches or
parses are recorded explicitly. A discovery failure never reports a complete
successful run.

- `ipduotest_pl_runs`: UUID, start/completion times, site URL, status
  (`ok`, `partial`, `error`) and discovery errors.
- `ipduotest_pl_resources`: one row per run and unique resource key, source
  URL, fetch timestamp, status/error, SHA-256, response headers and publishing
  configuration in `metadata`, and parsed sheet data in `parsed` JSONB.
- `ipduotest_pl_blobs`: original response bodies, including chart bytes,
  deduplicated by SHA-256 and stored as PostgreSQL `bytea`.
- `ipduotest_pl_latest`: latest attempt per resource, including failures.
- `ipduotest_pl_latest_success`: last successful fetch per resource. Check its
  timestamp and latest attempt before interpreting it as current.

For sheet resources, `parsed.headers` holds the first published data row,
`parsed.rows` holds every later row as an ordered array of original strings,
and `parsed.cells` preserves the header and data cell metadata. No row limit,
locale conversion, record aggregation or automatic correction is applied.

Original bodies also live at `data/ipduotest-pl/blobs/<sha256>`. A complete manifest
is written to `data/ipduotest-pl/pending/<run-id>.json` before database import. After
a successful transaction it moves to `data/ipduotest-pl/runs/<run-id>.json`. When
PostgreSQL is unavailable the collector continues capturing files and retries
pending imports later. UUID/content-hash keys make replay idempotent. Original
responses are retained even if HTML parsing fails. A database advisory lock
serializes imports; there should normally be one collecting service.

These files and database history are **not subject to ordinary frame pruning**.
Local image blobs under `data/` are still subject to the global 20 GB image cap;
original bytes already imported into PostgreSQL have no automatic expiry.
The quota can also evict pending image blobs before import, in which case replay
needs those missing bytes restored. See [image quota behavior](frame-retention.md#global-image-limit).
Back up both the database volume and `data/ipduotest-pl/`. File growth continues
while collecting. `proof-of-concept/test-data/2026-10-25-ipduotest-pl/` preserves
the initial homepage, table-loader script, source tables, chart, parsed data,
checksums and regression evidence. Keep historical fixtures unchanged.

If the site stops publishing a source, its last resource row remains in the
latest views. Consult the latest successful homepage's `parsed.sources` for
current membership, and its latest attempt for discovery errors. This avoids
silently treating historical sources as freshly discovered ones.

## Operations and queries

```sh
docker compose up -d --build ipduotest-pl
docker compose logs --tail 10 ipduotest-pl
```

A one-shot fetch (avoid running concurrent collectors unnecessarily):

```sh
docker compose run --rm ipduotest-pl python ipduotest_pl.py --once
```

Import already queued manifests without network requests:

```sh
docker compose run --rm ipduotest-pl python ipduotest_pl.py --replay-only
```

Inspect freshness and failures:

```sh
docker compose exec db psql -U folding -d folding -c "
SELECT resource_key, fetched_at AT TIME ZONE 'Europe/Warsaw' AS fetched_warsaw,
       status, error, parsed->>'row_count' AS published_rows
FROM ipduotest_pl_latest ORDER BY resource_key;"
```

Read every row of the latest successfully fetched overall history:

```sh
docker compose exec db psql -U folding -d folding -c "
SELECT r.fetched_at, row_number, cells
FROM ipduotest_pl_latest_success r
CROSS JOIN LATERAL jsonb_array_elements(r.parsed->'rows')
  WITH ORDINALITY AS source_row(cells, row_number)
WHERE r.resource_key = 'sheet:1GrqBCt6WzAPBa_mLNPHODPIJkem51wLg5cCatnYw9BM:0'
ORDER BY row_number;"
```

`row_number` here is the ordinal within the data array, not a named presenter
or a guaranteed stable source record ID. The original header is in
`parsed.headers`. JSONB can be exported using normal PostgreSQL queries; raw
bytes are available through `ipduotest_pl_blobs` joined on `sha256`.

Offline-fixture database verification (uses archived data, no website fetch):

```sh
docker compose run --rm -v "$PWD/proof-of-concept:/fixtures:ro" ipduotest-pl \
  python ipduotest_pl_integration_check.py
```

This verifies complete tables, exact raw bytes in PostgreSQL, database-outage
retention, and duplicate replay. Test runs use a separate UUID-named PostgreSQL schema (`ipduotest_pl_test_…`) so
fixture data never replaces live latest-source views. Test tables remain as
isolated history. Test evidence is preserved in
`data/test-evidence/ipduotest-pl/<run-id>/`, or `IPDUOTEST_PL_TEST_OUTPUT_DIR` when
set. The result records the test schema and run ID.

## Upgrading an existing archive

`make docker-upgrade` stops the previous collector before starting `ipduotest-pl`.
For a manual upgrade, run `bash utilities/stop-legacy-ipduotest-pl.sh` first so
that only one collector writes the archive. The new collector moves the legacy
`data/official/` directory to `data/ipduotest-pl/` and renames the legacy database
tables and views in place on its first import. Comparisons rename their source
run and pace columns in place as well. Existing snapshots and response bytes
are preserved. If both archive directories exist, collection stops for manual
consolidation rather than overwriting either archive.

Use `IPDUOTEST_PL_INTERVAL_SECONDS` in `.env` (replacing the previous
`OFFICIAL_DATA_INTERVAL_SECONDS` setting). Python and SQL identifiers use
`ipduotest_pl`; service names, paths and display labels use `ipduotest-pl`.
Source URLs and preserved response bytes continue to contain the actual domain.
