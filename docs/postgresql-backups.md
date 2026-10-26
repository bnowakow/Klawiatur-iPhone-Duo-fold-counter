# PostgreSQL backups

Adapted from `/Users/sup/code/cozadzban`'s `docker-pg-backup`,
`install-pg-backup-cron` and `ensure-pg-backup-cron` Make targets.

## Create a backup

```sh
make docker-pg-backup
```

The running Compose `db` service runs its own PostgreSQL 17 `pg_dump` against
`POSTGRES_DB` as `POSTGRES_USER`. Passwords remain in the container configuration;
they are not copied into shell arguments. A consistent logical SQL snapshot is
created with `--clean --if-exists --no-owner --no-privileges`, then compressed into
one ZIP file under **`docker-data/backup/postgres/`**. Filenames include a UTC
stamp and process ID, for example `fold-counter-postgres-2026-10-26_09-41-42Z-66424.zip`.
The live database volume and application processes are unchanged.

Unlike the original inline recipe, this implementation stops on dump/compression
failure and checks the ZIP before publishing it. Work files are private and are
removed on normal completion or script failure. The final ZIP is moved into place
only after successful completion. Backup directory permissions are `0700`, files
`0600`. Backups are excluded from Git and Docker build contexts.

Override the location when needed:

```sh
make docker-pg-backup PG_BACKUP_DIR=/absolute/backup/location
```

The archive includes PostgreSQL schemas, table data, views and database-held blobs
and audit history. It does not include global cluster roles/passwords, ownership/
privilege grants, the `data/` image files or SQLite retention ledger, `.env`, OAuth
credential files, or repository regression fixtures. Back up those files separately
when a full application recovery is required. The named `postgres_data` volume
provides persistence; these logical dumps provide an additional recovery copy.
Archives have no automatic expiry and are outside the 20 GB image quota. Copy them
to separate storage when protection from host/disk loss is required.

## Verify a backup by restoring it

```sh
make docker-pg-backup-verify BACKUP_FILE=docker-data/backup/postgres/<backup>.zip
```

This checks ZIP integrity and its single SQL member, starts an isolated
`postgres:17` container with no published ports or external network, waits for the
final database server, then restores with `psql --set ON_ERROR_STOP=1
--single-transaction`. It reports the restored public table count. Its anonymous
data volume and container are removed on completion or failure. It never connects
to or restores over the live Compose database. Temporary disk space is needed for
the restored database; it uses Docker storage, not the production database volume.

## Optional daily scheduling

The copied scheduling methods are available but installation is **opt-in**:

```sh
make install-pg-backup-cron
make ensure-pg-backup-cron
```

The default schedule is daily **02:00 in the host's timezone**. On this laptop,
that is Europe/Warsaw. `install` replaces only this project's managed entry;
`ensure` leaves an existing entry unchanged. Both preserve unrelated cron jobs,
including any existing `cozadzban` backup job. The marker is
`fold-counter-docker-pg-backup`. Output is appended to
`docker-data/backup/postgres/cron.log` (or the selected backup directory).
The job captures the executable PATH and make path so Docker Desktop/Homebrew
commands remain discoverable in cron's reduced environment.

```sh
make install-pg-backup-cron CRON_SCHEDULE='0 3 * * *'
```

The host must be awake with Docker running at the scheduled time; cron does not
catch up a missed run. Inspect `crontab -l` and `cron.log` to check installation
and outcomes. Scheduling is not installed by creating a manual backup or upgrading
the app. Host dependencies are Bash, Docker Compose, `zip`/`unzip`, and `make`;
cron installation additionally requires `crontab`.

## Initial validation

On 26 October 2026 a backup was created successfully and restored into a separate
PostgreSQL 17 instance: **36 public tables** restored without SQL errors. The
archive was approximately 45 MB and contained a 247 MB SQL dump. Six unit checks
also verified private ZIP output, failed dumps/compression leaving no published
backup, cron job preservation/idempotence, and cron error/input handling. Cron
commands in tests were mocked; no real host schedule was installed.
