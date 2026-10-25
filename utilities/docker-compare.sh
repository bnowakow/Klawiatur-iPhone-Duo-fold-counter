#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
command -v jq >/dev/null 2>&1 || { echo "jq is required for docker-compare." >&2; exit 1; }

report=$(mktemp "${TMPDIR:-/tmp}/fold-counter-comparison.XXXXXX")
trap 'rm -f "$report"' EXIT
status=0
docker compose run --rm -T official-data python compare_current_tester.py >"$report" || status=$?

# Exit 2 is a valid report with unavailable comparison evidence. Operational
# failures and malformed JSON still fail the command.
if [ "$status" -ne 0 ] && [ "$status" -ne 2 ]; then
    cat "$report" >&2
    exit "$status"
fi
jq -r -f utilities/compare-pace.jq "$report"
