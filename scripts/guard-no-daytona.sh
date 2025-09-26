#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

declare -a forbidden_patterns=(
  "daytona:Daytona"
  "octokit:Octokit"
  "api.github.com:GitHub API"
)

declare -a hits=()

for entry in "${forbidden_patterns[@]}"; do
  pattern="${entry%%:*}"
  label="${entry#*:}"

  mapfile -t matches < <(rg -i "${pattern}" --files-with-matches --hidden --no-ignore --glob '!**/.git/**' --glob '!**/vendor/**' --glob '!**/node_modules/**' --glob '!scripts/guard-no-daytona.sh' || true)

  if ((${#matches[@]})); then
    for file in "${matches[@]}"; do
      if [[ "${pattern}" == "daytona" ]]; then
        if ! rg -in "${pattern}" "${file}" | rg -vi 'guard[-:]no-daytona' >/dev/null; then
          continue
        fi
      fi
      hits+=("${file}::${label}")
    done
  fi
done

if ((${#hits[@]})); then
  printf 'ERROR: Forbidden references found in:\n'
  for hit in "${hits[@]}"; do
    file="${hit%%::*}"
    label="${hit#*::}"
    printf ' - %s (pattern: %s)\n' "$file" "$label"
  done
  exit 1
fi

printf 'OK: No forbidden references.\n'
