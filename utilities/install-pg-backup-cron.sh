#!/usr/bin/env bash
# Adapted from cozadzban's install/ensure-pg-backup-cron targets; opt-in only.
set -euo pipefail
umask 077
mode=${1:-install}
[[ "$mode" == install || "$mode" == ensure ]] || exit 2
project_root=$(cd "$(dirname "$0")/.." && pwd)
backup_dir=${PG_BACKUP_DIR:-docker-data/backup/postgres}
[[ "$backup_dir" == /* ]] || backup_dir="$project_root/$backup_dir"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
schedule=${CRON_SCHEDULE:-0 2 * * *}
marker=fold-counter-docker-pg-backup
make_bin=${CRON_MAKE:-$(command -v make)}
# Permit the usual five-field numeric cron syntax, not embedded shell commands.
if [[ "$schedule" == *$'\n'* || ! "$schedule" =~ ^[0-9*/,-]+[[:space:]]+[0-9*/,-]+[[:space:]]+[0-9*/,-]+[[:space:]]+[0-9*/,-]+[[:space:]]+[0-9*/,-]+$ ]]; then
    echo 'CRON_SCHEDULE must contain five numeric cron fields' >&2; exit 1
fi
work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT
if ! crontab -l > "$work_dir/current" 2> "$work_dir/error"; then
    if ! LC_ALL=C grep -qi 'no crontab' "$work_dir/error"; then
        cat "$work_dir/error" >&2; exit 1
    fi
    : > "$work_dir/current"
fi
if [[ "$mode" == ensure ]] && grep -Fq "# $marker" "$work_dir/current"; then
    echo 'PostgreSQL backup cron job already installed'; exit 0
fi
printf -v root_quoted '%q' "$project_root"
printf -v make_quoted '%q' "$make_bin"
printf -v path_quoted '%q' "$PATH"
printf -v dir_quoted '%q' "$backup_dir"
printf -v log_quoted '%q' "$backup_dir/cron.log"
job="$schedule cd $root_quoted && PATH=$path_quoted PG_BACKUP_DIR=$dir_quoted $make_quoted docker-pg-backup >> $log_quoted 2>&1 # $marker"
# Cron treats percent specially, even inside shell quotes.
job=${job//%/\\%}
{ grep -Fv "# $marker" "$work_dir/current" || true; printf '%s\n' "$job"; } > "$work_dir/updated"
crontab "$work_dir/updated"
printf 'Installed PostgreSQL backup cron job (%s, host timezone).\n' "$schedule"
