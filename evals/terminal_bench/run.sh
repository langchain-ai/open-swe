#!/usr/bin/env bash
# Robin on Terminal-Bench 2.0 through harbor-runner, for any model Robin offers.
# Keys come from the shell, secrets/ or open-swe/.env (README: Prerequisites).
#
#   ./run.sh smoke <model> [effort] [--personal-key]   2 tasks: follow, pull, delete the sandbox
#   ./run.sh full  <model> [effort] [--personal-key]   every task, detached
#   ./run.sh status|pull|attach|down [run]             the newest run unless named
#   ./run.sh ls
#
# <model> is Harbor's `provider/model`, e.g. anthropic/claude-opus-5-5 or google_genai/gemini-4-neon;
# effort defaults to medium. --personal-key accepts an lsv2_pt_ key: tasks then run as you.
set -euo pipefail
cd "$(dirname "$0")"

runner() { uv run --locked harbor-runner "$@"; }

usage() {
  sed -n '5,8p' "$0" | sed 's/^# //' >&2
  exit 2
}

command="${1:-}"
shift || true
case "$command" in
  smoke | full)
    model="${1:-}"
    [ -n "$model" ] || usage
    shift
    effort=medium
    if [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; then
      effort="$1"
      shift
    fi
    uv sync --locked
    if [ "$command" = smoke ]; then
      name="smoke-$(basename "$model" | tr '[:upper:]._' '[:lower:]--')-$effort-$(date -u +%H%M%S)"
      runner up "$model" "$effort" "$@" --name "$name" \
        -- -d terminal-bench@2.0 -i fix-git -i openssl-selfsigned-cert || true
      report="$(runner status "$name")"
      echo "$report"
      # A Ctrl-C detaches and leaves Harbor running, so keep the sandbox until it exits.
      if ! grep -q '^state: exited' <<<"$report"; then
        echo "Still running: \`$0 attach $name\` follows it, then \`$0 pull $name\` and \`$0 down $name\`." >&2
        exit 0
      fi
      runner pull "$name"
      runner down "$name" -y
    else
      runner up "$model" "$effort" "$@" --detach
      echo "Started. \`$0 status\` checks on it; \`$0 pull\` copies its results here when it's done."
    fi
    ;;
  status | pull | attach | down) runner "$command" "$@" ;;
  ls) runner ls ;;
  *) usage ;;
esac
