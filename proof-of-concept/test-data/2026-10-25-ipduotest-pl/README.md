# Public-site regression evidence

Initial public responses from https://ipduotest.pl/ and its referenced Google
Sheets views, captured on 2026-10-25. Keep these files unchanged when adding
new source layouts or parsers.

- `home.html` and `home.headers`: original homepage and HTTP headers.
- `frontend.js`: public table loader inspected to reproduce its read-only URLs.
- `tables-config.json`: published Sheetable configuration; IDs 1 and 2 share
  one sheet view. No private or authenticated endpoints were accessed.
- `sheet-1.html`, `sheet-3.html`, `sheet-4.html`: full public sheet responses.
- Matching `.json` files: parsed cell values and cell metadata.
- `chart.png`: original published chart bytes.
- `probe-result.json`: original resource URLs, sizes and content types.
- `unit-tests.txt`: 32 passing regression tests; the same suite passed in Compose.
- `integration/`: passing PostgreSQL verification, exact archived blobs and run
  manifest. Tests use an isolated PostgreSQL schema recorded in result.json.

Original response values can be stale or internally inconsistent; preserve
source strings rather than correcting them in these fixtures. See
`docs/ipduotest-pl.md` in the repository for source context and field meanings.
