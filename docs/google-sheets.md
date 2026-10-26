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
The local listener waits up to 30 minutes. If the browser reports that localhost
refused the connection after that window, run `make sheets-auth` again and open
the new Google authorization link; an old callback cannot complete a new flow.

```sh
make sheets-preview    # Read PostgreSQL only; show the data without writing to Google.
make sheets-status     # Verify authorization and the destination, without writes.
make sheets-sync       # Write/update the app's columns in the destination tab.
make sheets-disconnect # Remove local Sheets credentials without revoking YouTube.
```

The `sheets-worker` service performs scheduled exports after explicit startup
with `make sheets-job-up`. Enabling the API or authorizing alone does not start it. `sheets-sync` can be rerun safely after timeout: it updates existing stint
rows by UUID rather than appending duplicate results. An API batch is atomic.

## Columns and ownership

The first five columns match `Ranking` in order and wording:

| Column | Meaning |
| --- | --- |
| A — Data / Start Testera | Detected appearance time, not a scheduled hourly slot |
| B — Wynik na koniec | Last accepted global counter for a qualifying completed stint |
| C — Wynik indywidualny | Last accepted counter minus that stint's initial accepted counter |
| D — Czas składania (min) | Actual elapsed minutes between the accepted baseline and last reading |
| E — Złożenia na minutę | Individual folds divided by those elapsed minutes |
| F — Koniec zmiany | Detected or reviewed end timestamp |
| G — Licznik początkowy | Accepted baseline counter used in the subtraction |
| H — Czas ostatniego odczytu | Timestamp of the ending counter in B |
| I — Status danych | Completed-run status, attribution warning and end reason |
| J — ID zmiany | Permanent anonymous stint UUID used to keep a stable row |
| K/L — Nagranie — start/koniec | Existing video review bookmarks, when available |
| M — Aktualizacja | Snapshot generation time |
| N — Początek pomiaru | Timestamp of the baseline in G |
| O — opis osoby składającej | Stored completed Polish appearance description for this stint; blank if unavailable |

All displayed times use Europe/Warsaw. Video offsets remain capture-clock
estimates; missing links stay blank. Missing counters/start/finish evidence exclude a stint from the visible export. No data is invented for time before monitoring started. Separate
appearances by a returning person remain separate anonymous stints.

**A:O belong to the application. P onward belong to human reviewers.** Add
columns such as `Weryfikacja człowieka`, `Uwagi weryfikatora` and `Zweryfikował(a)`
there. The exporter never writes values, notes, widths or formatting to P onward.
It never sorts or deletes rows, so reviews remain attached to the same UUID.
Rows that do not qualify as complete are hidden in place, preserving their
measurements and human review cells. A previously hidden stint becomes visible
when it qualifies on a later sync.
Do not manually replace stint IDs or insert filled rows within A:O. Sorting
whole rows, including IDs and human notes, is supported. Correct the underlying
records through an audited repair so that the next export reflects the fix.

Initial export refuses to overwrite unrecognized existing content in A:O. It
adds an ownership/methodology note on A2, writes the headers on row 2 and applies the
reference appearance once. Later syncs update values without replacing cell
notes or reviewers' formatting, apart from the application-owned methodology
note on A2 and the numeric formats. Existing source rows missing from a snapshot
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

## Source row, descriptions and numeric display

Row 1 contains the static notice and source link: `dane generowane przez aplikację mogą posiadać błędy, do weryfikacji. źródło danych: https://github.com/bnowakow/Klawiatur-iPhone-Duo-fold-counter`.
Row 2 contains the column headers; data begins on row 3. Counts in B, C and G
use integer format `0`, avoiding the dangling comma produced by optional decimal
placeholders in the spreadsheet's Polish locale. Duration and pace show two
decimal places. Values are still numeric, not locale-dependent strings.

Column O uses the existing `presenter_clothing_descriptions.description` only
when `description_language='pl'` and `status='completed'`. No new description
is inferred by the exporter. These are automatic appearance descriptions, not
identity verification. Missing descriptions remain blank. Text wraps for reading.

A one-time atomic migration inserts the source row and inserts description
column O before the old reviewer columns. Existing reviewer cells move one row
down and one column right with their stint; values and notes are preserved.
Subsequent syncs do not repeat these insertions. Human review now begins at P.

## Only complete captured runs (2026-10-26)

Visible results require a recorded folding start, accepted baseline/ending
counters, positive elapsed measurement time, and a recorded folding finish.
The start must come from an observed or reviewed handover; first-ever
observations, manual baseline resets and first frames on returning after a night
pause are excluded because the person may already have been folding. The
accepted baseline must be timestamped at the detected starting boundary; a late
first valid counter excludes that stint too. Manual-reset ends do not qualify.

Recorded handover boundaries and night stops remain detector/review estimates,
not exact physical fold times. Qualifying results retain attribution warnings
for human verification. Partial runs are kept in PostgreSQL and previously
exported partial rows are hidden, not deleted, retaining reviewer notes.
The 328-fold first observation, the late morning resumption, the delayed-baseline
08:00 stint and the ongoing stint are excluded under this policy.

## Completed runs with delayed counters (policy update)

The user subsequently requested completed runs like the tester finishing around
09:00 as well. A captured/reviewed handover and an observed finish still qualify
even if the accepted baseline was delayed. Such rows are explicitly marked
`WYNIK CZĘŚCIOWY` in I, with the delay in seconds and a warning that earlier folds
are not included. Counts remain actual accepted-counter differences; missing
folds are never extrapolated. D/E use only the measured baseline-to-last-reading
interval. These counts must not be treated as complete personal records in
rankings or aggregate comparisons. The 08:00–08:59 stint is visible with 2,957
recorded folds and a 105-second delayed baseline. Ongoing runs, initial mid-run
captures, unobserved morning starts and manual resets remain hidden.

## Automatic hourly and stint-finish job

```sh
make sheets-job-up     # Build and start the background worker.
make sheets-job-status # Last success/attempt, next hourly run, failures and retry time (UTC).
make sheets-job-logs   # Recent triggers, successes and sanitized failures.
make sheets-job-stop  # Stop scheduled exports.
```

The worker performs an initial sync if none has succeeded, then syncs on each
hour at one minute past (for example 10:01, 11:01) and whenever a newly recorded finish or correction changes the
closed-session fingerprint. It checks PostgreSQL every 10 seconds; Google calls
are made only when a sync is due. Detection must first record the person's
finish, so the 10 seconds are after that database update, not the physical fold.
Multiple finishes in the same interval are covered by one snapshot.

A session-level PostgreSQL advisory lock covers the complete job: snapshot,
OAuth refresh, Google write and successful checkpoint. All manual and scheduled
commands use the same destination lock. If another sync holds it, a caller
skips rather than overlapping. A separate advisory lock permits only one
worker loop per destination. No spreadsheet or reviewer columns are used as locks.

Each job runs in a child process with a **180-second timeout**, including waits
for OAuth credentials. Timeout kills and reaps the child, releasing its DB/file
locks. A repeat safely updates the same UUID rows. Docker allows 190 seconds
for graceful shutdown of an in-flight job; it restarts the worker after failures
or a Docker restart. Docker must be running and the host awake.

`sheets_export_jobs` stores the last successful checkpoint, next hourly :01
deadline, closed-session fingerprint, attempts and retry status. A checkpoint
advances only after the Google write succeeds. Failures retry after 60 seconds,
then 120, 240, 480 and at most 900 seconds. Pending finishes and overdue hourly
runs survive restart. A finish-triggered or manual sync does not postpone the
next :01 run. Snapshots do not consume finishes recorded while a Google
write is running; those trigger the next sync.

The destination, eligibility, partial-result warnings and reviewer ownership
remain as documented above. `make docker-upgrade` refreshes an already running
Sheets worker. Test with `test_sheets_jobs.py` and
`sheets_jobs_integration_check.py`; the latter uses an isolated database schema
and mocks all Google writes.


Human-confirmed early finishes for a freeze test (`confirmed_freeze_test_pause`)
qualify for export when the stint's start and accepted endpoint are present,
including a partial result when the first baseline was delayed. The exporter
updates the existing UUID row with the final counter, folds and stop time rather
than leaving its old in-progress snapshot. After any data correction, run
`make sheets-sync` and verify the remote row, as required by `AGENTS.md`.
