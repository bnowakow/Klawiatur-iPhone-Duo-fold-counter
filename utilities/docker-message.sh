#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
message=$(docker compose exec -T supervisor python - < utilities/current_person_message.py)
if [ -z "$message" ]; then
    echo "No message was generated; clipboard unchanged." >&2
    exit 1
fi
printf '%s\n' "$message"
if command -v pbcopy >/dev/null 2>&1; then
    printf '%s' "$message" | pbcopy
else
    echo "Clipboard copy unavailable: pbcopy was not found. Copy the printed message manually." >&2
fi
