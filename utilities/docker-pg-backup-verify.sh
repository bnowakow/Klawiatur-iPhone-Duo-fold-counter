#!/usr/bin/env bash
# Restore only into an isolated throwaway container; never into the live db.
set -euo pipefail
umask 077
backup_file=${1:?Usage: docker-pg-backup-verify.sh BACKUP_FILE.zip}
backup_file=$(cd "$(dirname "$backup_file")" && pwd)/$(basename "$backup_file")
unzip -tqq "$backup_file"
entry=$(unzip -Z -1 "$backup_file")
if [[ "$entry" == *$'\n'* || ! "$entry" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*\.sql$ ]]; then
    echo 'Expected a ZIP containing exactly one flat SQL dump' >&2
    exit 1
fi
container="fold-counter-backup-verify-$(date -u +%s)-$$"
cleanup() { docker rm -f -v "$container" >/dev/null 2>&1 || true; }
trap cleanup EXIT
# Anonymous PostgreSQL storage is removed with --rm/-v. No host port or live volume.
docker run --detach --rm --network none --name "$container" \
    -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=folding -e POSTGRES_USER=folding \
    postgres:17 >/dev/null
ready=false
for attempt in {1..60}; do
    if docker exec "$container" pg_isready -h 127.0.0.1 -U folding -d folding >/dev/null 2>&1; then
        ready=true
        break
    fi
    sleep 1
done
if [[ "$ready" != true ]]; then
    echo 'Disposable verification PostgreSQL did not become ready' >&2
    exit 1
fi
printf 'Restoring %s into isolated container %s...\n' "$backup_file" "$container"
unzip -p "$backup_file" "$entry" | docker exec -i "$container" \
    psql -X -U folding -d folding --set ON_ERROR_STOP=1 --single-transaction >/dev/null
docker exec "$container" psql -X -U folding -d folding -Atc \
    "SELECT 'Restored public tables: ' || count(*) FROM pg_tables WHERE schemaname='public';"
printf 'Backup restore verified; disposable database will be removed.\n'
