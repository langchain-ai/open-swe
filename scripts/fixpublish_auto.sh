#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PUBLISH_CMD=(./scripts/fixpublish.sh "$@")

ANALYSIS_FILE=""
LAST_ANALYSIS_FILE=""

run_and_print() {
  local output_file
  output_file="$(mktemp)"
  if "$@" >"$output_file" 2>&1; then
    RUN_STATUS=0
  else
    RUN_STATUS=$?
  fi
  RUN_OUTPUT="$(cat "$output_file")"
  rm -f "$output_file"
  printf '%s' "$RUN_OUTPUT"
  if [[ -n "$RUN_OUTPUT" && "${RUN_OUTPUT: -1}" != $'\n' ]]; then
    printf '\n'
  fi
}

extract_pr_url() {
  local output="$1"
  local line
  while IFS= read -r line; do
    case "$line" in
      pr_url:\ *)
        local value="${line#pr_url: }"
        if [[ -n "$value" && "$value" != "(none)" ]]; then
          printf '%s\n' "$value"
          return 0
        fi
        ;;
      previous_pr_url:\ *)
        local value="${line#previous_pr_url: }"
        if [[ -n "$value" && "$value" != "(none)" ]]; then
          printf '%s\n' "$value"
          return 0
        fi
        ;;
    esac
  done <<<"$output"
  return 1
}

extract_block_reason() {
  local output="$1"
  local line
  local reason=""
  while IFS= read -r line; do
    case "$line" in
      Publish\ blocked\ because*)
        reason="$line"
        ;;
      reason:\ Publish\ blocked\ because*)
        reason="${line#reason: }"
        ;;
      validation_record_reason:\ *)
        reason="${line#validation_record_reason: }"
        ;;
    esac
  done <<<"$output"
  if [[ -n "$reason" ]]; then
    printf '%s\n' "$reason"
  else
    printf 'the publish workflow did not report a reusable PR URL\n'
  fi
}

publish_succeeded() {
  local output="$1"
  [[ "$output" == *"FINAL: validation succeeded, publish succeeded"* ]] \
    || [[ "$output" == *"FINAL: already published — PR:"* ]] \
    || [[ "$output" == *"FINAL: already published - PR:"* ]]
}

validation_blocked() {
  local output="$1"
  [[ "$output" == *"FINAL: validation failed"* ]] \
    || [[ "$output" == *"FINAL: validation blocked"* ]] \
    || [[ "$output" == *"validation_result: failed"* ]] \
    || [[ "$output" == *"validation_record_reason: "* ]] \
    || [[ "$output" == *"Publish blocked because the latest validation run failed"* ]] \
    || [[ "$output" == *"Publish blocked because auto-revalidation failed"* ]] \
    || [[ "$output" == *"revalidation failed"* ]] \
    || [[ "$output" == *"validation failed after"* ]]
}

finalize_result() {
  local output="$1"
  if publish_succeeded "$output"; then
    local pr_url
    pr_url="$(extract_pr_url "$output" || true)"
    if [[ -n "$pr_url" ]]; then
      printf 'A PR was created/reused at %s\n' "$pr_url"
      return 0
    fi
  fi
  local reason
  reason="$(extract_block_reason "$output")"
  reason="${reason#Publish blocked because }"
  reason="${reason#publish blocked because }"
  printf 'Publish blocked because %s\n' "$reason"
  return 1
}

analyze_validation_failure() {
  local output="$1"
  local output_file
  output_file="$(mktemp)"
  ANALYSIS_FILE="$(mktemp)"
  printf '%s' "$output" >"$output_file"
  if python local_fix_agent.py \
    --repo "$ROOT_DIR" \
    --analyze-validation-failure \
    --validation-output-file "$output_file" \
    --output json >"$ANALYSIS_FILE"; then
    :
  else
    printf '{"validation_error_type":"unknown","failing_command":"","failing_test_files":[],"failing_source_files":[],"repair_targets":[],"repair_goal":"Fix the validation failure blocking publish.","repair_context_used":false,"failure_context_snippet":"","analysis_source":"publish_output"}\n' >"$ANALYSIS_FILE"
  fi
  rm -f "$output_file"
  LAST_ANALYSIS_FILE="$ANALYSIS_FILE"
}

print_analysis() {
  local analysis_file="$1"
  python - "$analysis_file" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    data = json.load(fh)

print(f"validation_error_type: {data.get('validation_error_type') or 'unknown'}")
print(f"failing_test_files: {data.get('failing_test_files') or []}")
print(f"failing_source_files: {data.get('failing_source_files') or []}")
print(f"traceback_files: {data.get('traceback_files') or []}")
print(f"failure_line_numbers: {data.get('failure_line_numbers') or []}")
print(f"repair_targets: {data.get('repair_targets') or []}")
print(f"repair_target_details: {data.get('repair_target_details') or []}")
print(f"target_confidence: {data.get('target_confidence') or 'low'}")
print(f"target_reason: {data.get('target_reason') or '(none)'}")
print(f"repair_context_used: {'true' if data.get('repair_context_used') else 'false'}")
snippet = data.get("failure_context_snippet") or ""
if snippet:
    print(f"failure_context_snippet: {snippet}")
PY
}

build_repair_command() {
  local analysis_file="$1"
  python - "$analysis_file" "$ROOT_DIR" <<'PY'
import json
import shlex
import sys

analysis_path = sys.argv[1]
root = sys.argv[2]
with open(analysis_path, "r", encoding="utf-8") as fh:
    data = json.load(fh)

cmd = [
    "python",
    "local_fix_agent.py",
    "--repo",
    root,
    "--mode",
    "quick",
    "--max-steps",
    "2",
    "--no-upstream-sync",
    "--repair-context-file",
    analysis_path,
]
failing_command = str(data.get("failing_command") or "").strip()
if failing_command and not failing_command.startswith("n/a ("):
    cmd.extend(["--test-cmd", failing_command])
else:
    cmd.append("--reuse-last-test")

print(" ".join(shlex.quote(part) for part in cmd))
PY
}

run_and_print "${PUBLISH_CMD[@]}"
if publish_succeeded "$RUN_OUTPUT"; then
  finalize_result "$RUN_OUTPUT"
  exit 0
fi
if ! validation_blocked "$RUN_OUTPUT"; then
  finalize_result "$RUN_OUTPUT"
  exit 1
fi

echo "Validation failed; attempting repair before one final publish retry."
analyze_validation_failure "$RUN_OUTPUT"
print_analysis "$ANALYSIS_FILE"
REPAIR_COMMAND="$(build_repair_command "$ANALYSIS_FILE")"
echo "repair_command: $REPAIR_COMMAND"
run_and_print bash -lc "$REPAIR_COMMAND"

run_and_print "${PUBLISH_CMD[@]}"
if validation_blocked "$RUN_OUTPUT"; then
  analyze_validation_failure "$RUN_OUTPUT"
  print_analysis "$ANALYSIS_FILE"
fi
if publish_succeeded "$RUN_OUTPUT"; then
  finalize_result "$RUN_OUTPUT"
  exit 0
fi

finalize_result "$RUN_OUTPUT"
