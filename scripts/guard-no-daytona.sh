#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mapfile -t matches < <(rg -i "daytona" --files-with-matches --hidden --no-ignore --glob '!vendor/**' --glob '!node_modules/**' --glob '!scripts/guard-no-daytona.sh')

if ((${#matches[@]})); then
  printf 'ERROR: Forbidden Daytona references found in:\n'
  printf ' - %s\n' "${matches[@]}"
  exit 1
fi

printf 'OK: No Daytona references.\n'
