#!/usr/bin/env bash
set -u

ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ERP_ROOT="${ERP_ROOT:-/erp}"
HARNESS_VENV="${HARNESS_VENV:-/home/olle/northstar-agent-harness/open-swe-python-runtime-check/.venv}"

gate="PASS"
warn() {
  gate="WARN"
  printf 'WARN %s\n' "$*"
}
fail() {
  gate="FAIL"
  printf 'FAIL %s\n' "$*"
}
pass() {
  printf 'PASS %s\n' "$*"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

printf 'NORTHSTAR_HARNESS_READINESS root=%s\n' "$ROOT"
printf 'generated_at=%s\n\n' "$(date -Is)"

printf '== Workspace boundary ==\n'
root_real="$(realpath "$ROOT" 2>/dev/null || printf '%s' "$ROOT")"
erp_real="$(realpath "$ERP_ROOT" 2>/dev/null || printf '%s' "$ERP_ROOT")"
printf 'root_real=%s\n' "$root_real"
printf 'erp_real=%s\n' "$erp_real"
case "$root_real" in
  "$erp_real"|"$erp_real"/*)
    fail "agent harness is inside /erp; move it outside the production workspace before bootstrap"
    ;;
  *)
    pass "agent harness is outside /erp"
    ;;
esac

printf '== Python ==\n'
if have python3.14.5; then
  version="$(python3.14.5 --version 2>&1)"
  path="$(readlink -f "$(command -v python3.14.5)")"
  pass "python3.14.5 available: ${version} at ${path}"
  if python3.14.5 - <<'PY' >/tmp/northstar-python3145-modules.out 2>&1
import bz2, ctypes, curses, hashlib, lzma, readline, sqlite3, ssl, tkinter, zlib
print("PY3145_FULL_MODULES=PASS")
PY
  then
    pass "python3.14.5 full module smoke"
  else
    fail "python3.14.5 missing expected optional modules"
    sed -n '1,20p' /tmp/northstar-python3145-modules.out
  fi
else
  fail "python3.14.5 not on PATH"
fi

if [ -x "$HARNESS_VENV/bin/python" ]; then
  pass "Open SWE runtime venv exists: $($HARNESS_VENV/bin/python --version 2>&1)"
  if "$HARNESS_VENV/bin/python" - <<'PY' >/tmp/northstar-openswe-runtime-modules.out 2>&1
import bz2, lzma, sqlite3, ssl, zlib
print("OPEN_SWE_RUNTIME_MODULES=PASS")
PY
  then
    pass "Open SWE runtime module smoke"
  else
    fail "Open SWE runtime missing expected modules"
    sed -n '1,20p' /tmp/northstar-openswe-runtime-modules.out
  fi
else
  fail "Open SWE runtime venv missing at $HARNESS_VENV"
fi

if have uv; then
  if uv python list 2>/tmp/northstar-uv-python-list.err | grep -q 'cpython-3.13.13'; then
    pass "uv-managed Python 3.13.13 is installed/listed"
  else
    warn "uv-managed Python 3.13.13 not found in uv python list"
    sed -n '1,20p' /tmp/northstar-uv-python-list.err
  fi
fi

printf '\n== Tooling ==\n'
for tool in git gh uv node npm pnpm rg jq gitleaks trufflehog osv-scanner pip-audit syft grype; do
  if have "$tool"; then
    pass "$tool available at $(command -v "$tool")"
  else
    fail "$tool missing"
  fi
done

printf '\n== Docker ==\n'
if have docker; then
  current_user="$(id -un 2>/dev/null || printf '%s' "${USER:-unknown}")"
  current_groups="$(id -nG 2>/dev/null || true)"
  account_groups="$(id -nG "$current_user" 2>/dev/null || printf '%s' "$current_groups")"
  current_has_docker_group=0
  account_has_docker_group=0
  printf 'current_user=%s\n' "$current_user"
  printf 'current_process_groups=%s\n' "$current_groups"
  printf 'account_groups=%s\n' "$account_groups"
  if printf '%s\n' "$current_groups" | tr ' ' '\n' | grep -qx 'docker'; then
    current_has_docker_group=1
  fi
  if printf '%s\n' "$account_groups" | tr ' ' '\n' | grep -qx 'docker'; then
    account_has_docker_group=1
  fi
  if [ "$current_has_docker_group" = "1" ]; then
    pass "current process is in docker group"
  elif [ "$account_has_docker_group" = "1" ]; then
    printf 'INFO account is in docker group, but this process has not refreshed supplementary groups\n'
  else
    warn "current user is not in docker group; docker group membership is root-equivalent and needs explicit approval"
  fi
  if docker info >/tmp/northstar-docker-info.out 2>&1; then
    if [ "$current_has_docker_group" = "1" ]; then
      pass "docker daemon reachable without sudo"
    elif [ "$account_has_docker_group" = "1" ]; then
      pass "docker daemon reachable through PATH wrapper/fresh-login bridge"
    else
      pass "docker daemon reachable"
    fi
  else
    warn "docker daemon not reachable by current user; sandbox image builds will need sudo or docker-group/relogin"
    sed -n '1,3p' /tmp/northstar-docker-info.out
  fi
else
  fail "docker CLI missing"
fi

printf '\n== Kernel/host capacity ==\n'
if sysctl fs.inotify.max_user_watches fs.inotify.max_user_instances fs.inotify.max_queued_events >/tmp/northstar-inotify.out 2>&1; then
  cat /tmp/northstar-inotify.out
  watches="$(awk '/max_user_watches/ {print $3}' /tmp/northstar-inotify.out)"
  instances="$(awk '/max_user_instances/ {print $3}' /tmp/northstar-inotify.out)"
  queued="$(awk '/max_queued_events/ {print $3}' /tmp/northstar-inotify.out)"
  if [ "${watches:-0}" -ge 524288 ] && [ "${instances:-0}" -ge 1024 ] && [ "${queued:-0}" -ge 32768 ]; then
    pass "inotify limits match Northstar large-workspace baseline"
  else
    warn "inotify limits below Northstar baseline"
  fi
else
  warn "could not read inotify sysctl values"
fi

free -h | sed 's/^/MEM /'
df -h "$ROOT" "$ERP_ROOT" /home 2>/dev/null | sed 's/^/DISK /'
nofile="$(ulimit -n 2>/dev/null || echo 0)"
printf 'ulimit_nofile=%s\n' "$nofile"
if [ "${nofile:-0}" -ge 8192 ]; then
  pass "open-file limit is adequate for local harness and Node tooling"
else
  warn "open-file limit below 8192; large Nx/Node/Hermes runs may hit EMFILE under load"
fi

printf '\n== Hermes/Northstar coordination ==\n'
if have hermes; then
  if hermes auth status openai-codex >/tmp/northstar-hermes-auth.out 2>&1; then
    pass "Hermes openai-codex auth command succeeded: $(tr '\n' ' ' </tmp/northstar-hermes-auth.out)"
  else
    fail "Hermes openai-codex auth check failed"
    sed -n '1,20p' /tmp/northstar-hermes-auth.out
  fi
else
  fail "hermes CLI missing"
fi

if have gh; then
  if gh auth status >/tmp/northstar-gh-auth.out 2>&1; then
    pass "GitHub CLI auth status succeeded"
  else
    warn "GitHub CLI auth status failed"
    sed -n '1,20p' /tmp/northstar-gh-auth.out
  fi
else
  fail "gh CLI missing"
fi

if [ -x "$ERP_ROOT/kanban/agent-control.mjs" ] || [ -f "$ERP_ROOT/kanban/agent-control.mjs" ]; then
  if node "$ERP_ROOT/kanban/agent-control.mjs" status --json >/tmp/northstar-agent-control.json 2>/tmp/northstar-agent-control.err; then
    active="$(jq -r '.result.capacity.active // 0' /tmp/northstar-agent-control.json 2>/dev/null || echo 0)"
    edit="$(jq -r '.result.capacity.edit // 0' /tmp/northstar-agent-control.json 2>/dev/null || echo 0)"
    stale="$(jq -r '(.result.stale // []) | length' /tmp/northstar-agent-control.json 2>/dev/null || echo 0)"
    printf 'agent_control_active=%s edit=%s stale=%s\n' "$active" "$edit" "$stale"
    if [ "$stale" != "0" ]; then
      warn "agent-control has stale agents"
    elif [ "$active" != "0" ]; then
      warn "agent-control has active agents; avoid overlapping /erp scopes"
    else
      pass "agent-control has no active/stale agents"
    fi
  else
    warn "agent-control status failed"
    sed -n '1,20p' /tmp/northstar-agent-control.err
  fi
else
  warn "agent-control script not found under $ERP_ROOT"
fi

printf '\n== Secret/env hygiene ==\n'
if [ -f "$HOME/.hermes/.env" ]; then
  names="$(cut -d= -f1 "$HOME/.hermes/.env" | grep -E '^(GITHUB_TOKEN|GH_TOKEN|LANGSMITH|OPENAI|ANTHROPIC|EXA|TAVILY|FIRECRAWL|PARALLEL)' | sort | paste -sd, - || true)"
  printf 'hermes_env_secret_names=%s\n' "${names:-none}"
  if printf '%s\n' "$names" | grep -q 'GITHUB_TOKEN'; then
    pass "Hermes env has GITHUB_TOKEN name present"
  else
    warn "Hermes env lacks GITHUB_TOKEN; GitHub API checks may hit 60 req/hour unauthenticated limit"
  fi
else
  warn "Hermes .env missing"
fi

printf '\nNORTHSTAR_HARNESS_READINESS_GATE=%s\n' "$gate"
if [ "$gate" = "FAIL" ]; then
  exit 1
fi
exit 0
