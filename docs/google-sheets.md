# Google Sheets export for human verification

The exporter writes only to the `Klawiatur-iPhone-Duo-fold-counter` tab in
[the shared spreadsheet](https://docs.google.com/spreadsheets/d/1GrqBCt6WzAPBa_mLNPHODPIJkem51wLg5cCatnYw9BM/edit).
`Ranking` (original tab ID 0) is the read-only layout reference. Its published
layout was inspected on 2026-10-26: dark green headers, white text, compact font,
light alternate rows and five measurement columns. The exporter recreates that
presentation; it does not copy the source's manually entered results or formulas.

## Setup and commands

Google Sheets API must be enabled in the existing Google Cloud project from
`YOUTUBE_PROJECT_ID`. The existing destination tab is
`Klawiatur-iPhone-Duo-fold-counter` (tab ID `2091633028`). Its title and ID are
both pinned in code; deleting and recreating it requires an explicit code update. The app deliberately
does not create, rename or delete tabs.

```sh
make sheets-build
make sheets-auth
```

Open the printed Google consent link with the account that can edit the sheet,
and grant Sheets access. This reuses `secrets/youtube/client.json` and the existing
registered `http://localhost:8765/oauth/callback` redirect. Sheets authorization
is stored independently at `secrets/sheets/credentials.json`, with private file
permissions. The OAuth client is mounted read-only. The loopback port must be
free; do not run YouTube and Sheets authorization simultaneously.

```sh
make sheets-preview    # Read PostgreSQL only; show the data without writing to Google.
make sheets-status     # Verify authorization and the destination, without writes.
make sheets-sync       # Write/update the app's columns in the destination tab.
make sheets-disconnect # Remove local Sheets credentials without revoking YouTube.
```

Sync is manual; enabling the API or authorizing access does not start a recurring
export. `sheets-sync` can be rerun safely after timeout: it updates existing stint
rows by UUID rather than appending duplicate results. An API batch is atomic.

## Columns and ownership

The first five columns match `Ranking` in order and wording:

| Column | Meaning |
| --- | --- |
| A — Data / Start Testera | Detected appearance time, not a scheduled hourly slot |
| B — Wynik na koniec | Last accepted global counter; provisional while the stint is active |
| C — Wynik indywidualny | Last accepted counter minus that stint's initial accepted counter |
| D — Czas składania (min) | Actual elapsed minutes between the accepted baseline and last reading |
| E — Złożenia na minutę | Individual folds divided by those elapsed minutes |
| F — Koniec zmiany | Detected or reviewed end timestamp |
| G — Licznik początkowy | Accepted baseline counter used in the subtraction |
| H — Czas ostatniego odczytu | Timestamp of the ending counter in B |
| I — Status danych | Active/completed, missing readings, attribution warning and end reason |
| J — ID zmiany | Permanent anonymous stint UUID used to keep a stable row |
| K/L — Nagranie — start/koniec | Existing video review bookmarks, when available |
| M — Aktualizacja | Snapshot generation time |
| N — Początek pomiaru | Timestamp of the baseline in G |

All displayed times use Europe/Warsaw. Video offsets remain capture-clock
estimates; missing links stay blank. Missing counts/minutes/pace stay blank,
not zero. No data is invented for time before monitoring started. Separate
appearances by a returning person remain separate anonymous stints.

**A:N belong to the application. O onward belong to human reviewers.** Add
columns such as `Weryfikacja człowieka`, `Uwagi weryfikatora` and `Zweryfikował(a)`
there. The exporter never writes values, notes, widths or formatting to O onward.
It never sorts or deletes rows, so reviews remain attached to the same UUID.
Do not manually replace stint IDs or insert filled rows within A:N. Sorting
whole rows, including IDs and human notes, is supported. Correct the underlying
records through an audited repair so that the next export reflects the fix.

Initial export refuses to overwrite unrecognized existing content in A:N. It
adds an ownership/methodology note on A1, writes the headers and applies the
reference appearance once. Later syncs update values without replacing cell
notes or reviewers' formatting. Existing source rows missing from a snapshot
are retained rather than deleted; an empty database result cannot clear reviews.

Google OAuth scopes cannot be restricted to one tab. The app uses the Sheets
scope and enforces the fixed spreadsheet ID, exact title and numeric destination
tab ID in code, rejects the original Ranking ID, and validates each write. There
are no arbitrary spreadsheet/range options or destructive sheet operations.
See [Google's scope documentation](https://developers.google.com/workspace/sheets/api/scopes).

## Validation

```sh
python -m unittest discover -s proof-of-concept -p 'test_sheets_export.py'
```

Tests cover row stability, preservation of human columns, wrong-tab rejection,
ownership checks, missing readings, count/pace calculations, text-only handling
of formula-like strings and private, separate authorization storage. A real Google
write requires browser consent; preview and offline tests do not modify Sheets.
