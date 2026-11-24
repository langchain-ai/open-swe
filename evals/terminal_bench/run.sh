#!/usr/bin/env bash
# Robin on Terminal-Bench 2.0 through harbor-runner, for any model Robin offers.
# Keys come from the shell, secrets/ or open-swe/.env (README: Prerequisites).
#
#   ./run.sh smoke <model> [effort] [--personal-key]   2 tasks: follow, pull, delete the sandbox
#   ./run.sh full  <model> [effort] [--personal-key]   every task, detached
#   ./run.sh status|pull|attach|down [run]             the newest run unless named
#   ./run.sh ls
#   ./run.sh gh <model> [effort] [task glob…] [-- harbor args]   in GitHub Actions, then watch it
#
# <model> is Harbor's `provider/model`, e.g. anthropic/claude-opus-5-5 or google_genai/gemini-4-neon;
# effort defaults to medium. --personal-key accepts an lsv2_pt_ key: tasks then run as you.
set -euo pipefail
cd "$(dirname "$0")"

runner() { uv run --locked harbor-runner "$@"; }

usage() {
  sed -n '5,9p' "$0" | sed 's/^# //' >&2
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
  gh)
    # The "Robin: Terminal-Bench" workflow, against staging; ROBIN_TB_REF picks its branch and
    # ROBIN_TB_DATASET its Harbor dataset.
    model="${1:-}"
    [ -n "$model" ] || usage
    shift
    effort=medium
    if [ $# -gt 0 ] && [ "$1" != -- ]; then
      effort="$1"
      shift
    fi
    tasks=()
    while [ $# -gt 0 ] && [ "$1" != -- ]; do
      tasks+=("$1")
      shift
    done
    [ $# -gt 0 ] && shift
    out="$(gh workflow run robin_terminal_bench.yml --ref "${ROBIN_TB_REF:-main}" \
      -f model="$model" -f effort="$effort" -f dataset="${ROBIN_TB_DATASET:-terminal-bench@2.0}" -f tasks="${tasks[*]:-*}" -f harbor_args="$*")"
    echo "$out"
    run_id="$(grep -oE 'actions/runs/[0-9]+' <<<"$out" | cut -d/ -f3 || true)"
    if [ -z "$run_id" ]; then
      echo "Started; \`gh run list --workflow robin_terminal_bench.yml\` finds it." >&2
      exit 0
    fi
    # Ctrl-C only stops watching; the run continues.
    gh run watch "$run_id" --exit-status
    ;;
  status | pull | attach | down) runner "$command" "$@" ;;
  ls) runner ls ;;
  *) usage ;;
esac
