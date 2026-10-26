#!/usr/bin/env bash
# Retire the previous source collector before its archive/schema is migrated.
set -euo pipefail
docker compose ps --all --format '{{.Service}} {{.ID}}' |
  while read -r service container_id; do
    if [[ "$service" == official-data ]]; then
      docker stop "$container_id"
    fi
  done
