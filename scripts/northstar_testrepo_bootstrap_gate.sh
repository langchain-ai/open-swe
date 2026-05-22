#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env.example}"
READINESS_SCRIPT="${READINESS_SCRIPT:-$ROOT/scripts/northstar_local_readiness.sh}"
RUN_READINESS="${RUN_READINESS:-1}"

gate="PASS"

pass() {
  printf 'PASS %s\n' "$*"
}

warn() {
  if [ "$gate" = "PASS" ]; then
    gate="WARN"
  fi
  printf 'WARN %s\n' "$*"
}

fail() {
  gate="FAIL"
  printf 'FAIL %s\n' "$*"
}

env_value() {
  local key="$1"
  awk -v key="$key" '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
    {
      line = $0
      sub(/^[[:space:]]*export[[:space:]]+/, "", line)
      eq = index(line, "=")
      if (!eq) { next }
      name = substr(line, 1, eq - 1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      if (name == key) {
        value = substr(line, eq + 1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
        if (value ~ /^".*"$/) {
          value = substr(value, 2, length(value) - 2)
        }
        print value
        exit
      }
    }
  ' "$ENV_FILE"
}

upper_value() {
  printf '%s' "$1" | tr '[:lower:]' '[:upper:]'
}

is_yes() {
  case "$(upper_value "$1")" in
    YES | TRUE | 1)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

csv_contains() {
  local haystack="$1"
  local needle="$2"
  printf '%s' "$haystack" | tr ',' '\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' |
    tr '[:upper:]' '[:lower:]' | grep -qx "$(printf '%s' "$needle" | tr '[:upper:]' '[:lower:]')"
}

is_placeholder_or_empty() {
  local value="$1"
  [ -z "$value" ] && return 0
  case "$value" in
    REPLACE_WITH_* | PLACEHOLDER_* | *_PLACEHOLDER_* | example-* | *".invalid")
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

require_no_approval_flag() {
  local key="$1"
  local value
  value="$(env_value "$key")"
  if is_yes "$value"; then
    fail "$key is YES; this gate is for planning only and must not authorize external setup"
  else
    pass "$key is not enabled"
  fi
}

require_placeholder() {
  local key="$1"
  local value
  value="$(env_value "$key")"
  if is_placeholder_or_empty "$value"; then
    pass "$key is empty or placeholder-only"
  else
    fail "$key has a non-placeholder value; do not run this planning gate with real secrets"
  fi
}

require_empty() {
  local key="$1"
  local value
  value="$(env_value "$key")"
  if [ -z "$value" ]; then
    pass "$key is empty"
  else
    fail "$key is set; optional integration must stay disabled for testrepo-first bootstrap"
  fi
}

printf 'NORTHSTAR_TESTREPO_BOOTSTRAP_CHECK root=%s\n' "$ROOT"
printf 'generated_at=%s\n' "$(date -Is)"
printf 'env_file=%s\n\n' "$ENV_FILE"

if [ ! -f "$ENV_FILE" ]; then
  fail "env file missing: $ENV_FILE"
  printf '\nNORTHSTAR_TESTREPO_BOOTSTRAP_GATE=%s\n' "$gate"
  exit 1
fi

printf '== Local readiness ==\n'
if [ "$RUN_READINESS" = "1" ]; then
  if [ ! -x "$READINESS_SCRIPT" ]; then
    fail "readiness script is not executable: $READINESS_SCRIPT"
  else
    readiness_out="$(mktemp)"
    if "$READINESS_SCRIPT" "$ROOT" >"$readiness_out" 2>&1; then
      readiness_gate="$(awk -F= '/^NORTHSTAR_HARNESS_READINESS_GATE=/ {print $2}' "$readiness_out" | tail -1)"
      if [ "$readiness_gate" = "PASS" ]; then
        pass "northstar_local_readiness gate PASS"
      else
        fail "northstar_local_readiness gate was ${readiness_gate:-missing}"
        sed -n '1,80p' "$readiness_out"
      fi
    else
      fail "northstar_local_readiness command failed"
      sed -n '1,80p' "$readiness_out"
    fi
    rm -f "$readiness_out"
  fi
else
  warn "readiness check skipped by RUN_READINESS=$RUN_READINESS"
fi

printf '\n== Hard no-exposure flags ==\n'
for key in ALLOW_BOOTSTRAP_INSTALL ALLOW_WEBHOOK_SETUP ALLOW_GITHUB_APP_SETUP ALLOW_PROD_INSTALL ALLOW_DOCKER_BUILD; do
  require_no_approval_flag "$key"
done

printf '\n== Test repository profile ==\n'
default_owner="$(env_value DEFAULT_REPO_OWNER)"
default_name="$(env_value DEFAULT_REPO_NAME)"
allowed_repos="$(env_value ALLOWED_GITHUB_REPOS)"
reviewer_allowed_repos="$(env_value ALLOWED_REVIEWER_GITHUB_REPOS)"
allow_northstar="$(env_value ALLOW_NORTHSTAR_REPO)"
default_full="$(printf '%s/%s' "$default_owner" "$default_name" | tr '[:upper:]' '[:lower:]')"

if [ -n "$default_owner" ] && [ -n "$default_name" ]; then
  pass "default repo is declared as $default_full"
else
  fail "DEFAULT_REPO_OWNER and DEFAULT_REPO_NAME must be set for testrepo plan"
fi

if [ -n "$allowed_repos" ]; then
  pass "ALLOWED_GITHUB_REPOS is non-empty"
else
  fail "ALLOWED_GITHUB_REPOS is empty; Northstar profile must fail closed"
fi

if [ -n "$reviewer_allowed_repos" ]; then
  pass "ALLOWED_REVIEWER_GITHUB_REPOS is non-empty"
else
  fail "ALLOWED_REVIEWER_GITHUB_REPOS is empty; reviewer profile must fail closed"
fi

if [ -n "$default_full" ] && csv_contains "$allowed_repos" "$default_full"; then
  pass "default repo is included in ALLOWED_GITHUB_REPOS"
else
  fail "default repo is not included in ALLOWED_GITHUB_REPOS"
fi

if [ -n "$default_full" ] && csv_contains "$reviewer_allowed_repos" "$default_full"; then
  pass "default repo is included in ALLOWED_REVIEWER_GITHUB_REPOS"
else
  fail "default repo is not included in ALLOWED_REVIEWER_GITHUB_REPOS"
fi

if { [ "$default_full" = "ollehillbom1/north-star-erp" ] || csv_contains "$allowed_repos" "ollehillbom1/north-star-erp"; } &&
  ! is_yes "$allow_northstar"; then
  fail "Northstar ERP repo is selected before explicit ALLOW_NORTHSTAR_REPO=YES"
else
  pass "Northstar ERP repo is not selected for this testrepo-first plan"
fi

if [ "$(env_value REPO_ALLOWLIST_FAIL_CLOSED)" = "true" ]; then
  pass "REPO_ALLOWLIST_FAIL_CLOSED policy is declared"
else
  fail "REPO_ALLOWLIST_FAIL_CLOSED must be true for Northstar harness profile"
fi

printf '\n== Sandbox and integration policy ==\n'
sandbox_type="$(env_value SANDBOX_TYPE)"
case "$sandbox_type" in
  local)
    fail "SANDBOX_TYPE=local is not allowed for autonomous testrepo bootstrap"
    ;;
  "")
    fail "SANDBOX_TYPE is not set"
    ;;
  *)
    pass "SANDBOX_TYPE is non-local: $sandbox_type"
    ;;
esac

disabled_webhooks="$(env_value DISABLED_WEBHOOKS)"
for webhook in slack linear; do
  if csv_contains "$disabled_webhooks" "$webhook"; then
    pass "$webhook webhook is disabled by profile"
  else
    fail "$webhook webhook must be disabled for initial GitHub-only testrepo plan"
  fi
done

disabled_tools="$(env_value DISABLED_AGENT_TOOLS)"
for tool in \
  http_request fetch_url web_search \
  linear_comment linear_create_issue linear_delete_issue linear_get_issue \
  linear_get_issue_comments linear_list_teams linear_update_issue \
  slack_read_thread_messages slack_thread_reply; do
  if csv_contains "$disabled_tools" "$tool"; then
    pass "$tool disabled by profile"
  else
    fail "$tool must be disabled by initial profile"
  fi
done

printf '\n== Placeholder-only secrets ==\n'
for key in \
  LANGSMITH_API_KEY_PROD LANGSMITH_TENANT_ID_PROD LANGSMITH_TRACING_PROJECT_ID_PROD \
  ANTHROPIC_API_KEY OPENAI_API_KEY GOOGLE_API_KEY \
  GITHUB_APP_ID GITHUB_APP_PRIVATE_KEY GITHUB_APP_INSTALLATION_ID \
  GITHUB_WEBHOOK_SECRET GITHUB_OAUTH_PROVIDER_ID GITHUB_APP_CLIENT_ID \
  GITHUB_APP_CLIENT_SECRET DASHBOARD_JWT_SECRET TOKEN_ENCRYPTION_KEY \
  DEFAULT_SANDBOX_SNAPSHOT_ID; do
  require_placeholder "$key"
done

printf '\n== Optional external integrations ==\n'
for key in \
  LINEAR_API_KEY LINEAR_WEBHOOK_SECRET SLACK_BOT_TOKEN SLACK_BOT_USER_ID \
  SLACK_BOT_USERNAME SLACK_SIGNING_SECRET EXA_API_KEY; do
  require_empty "$key"
done

printf '\nNORTHSTAR_TESTREPO_BOOTSTRAP_GATE=%s\n' "$gate"
if [ "$gate" = "FAIL" ]; then
  exit 1
fi
exit 0
