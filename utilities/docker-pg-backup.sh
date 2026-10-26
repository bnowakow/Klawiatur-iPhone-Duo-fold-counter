#!/usr/bin/env bash
# Adapted from cozadzban's docker-pg-backup: logical SQL dump in a timestamped ZIP.
set -euo pipefail
umask 077
project_root=$(cd "$(dirname "$0")/.." && pwd)
cd "$project_root"
backup_dir=${PG_BACKUP_DIR:-docker-data/backup/postgres}
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
backup_dir=$(cd "$backup_dir" && pwd)
work_dir=$(mktemp -d "$backup_dir/.backup-work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT
base="fold-counter-postgres-$(date -u +%Y-%m-%d_%H-%M-%SZ)-$$"
sql_path="$work_dir/$base.sql"
zip_path="$work_dir/$base.zip"
printf 'Dumping PostgreSQL from Compose service db...\n'
docker compose -f compose.yaml exec -T db sh -ec \
    'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-privileges' > "$sql_path"
test -s "$sql_path"
zip -q -j "$zip_path" "$sql_path"
unzip -tqq "$zip_path"
mv "$zip_path" "$backup_dir/$base.zip"
printf 'Backup written to %s\n' "$backup_dir/$base.zip"
