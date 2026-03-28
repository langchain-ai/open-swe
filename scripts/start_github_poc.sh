#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f ".env" ]]; then
  echo "Missing .env in $ROOT_DIR"
  echo "Create it from .env.example first."
  exit 1
fi

required_vars=(
  SANDBOX_TYPE
  GITHUB_APP_ID
  GITHUB_APP_PRIVATE_KEY
  GITHUB_APP_INSTALLATION_ID
  GITHUB_WEBHOOK_SECRET
  ALLOWED_GITHUB_ORGS
  ALLOWED_GITHUB_REPOS
  DEFAULT_REPO_OWNER
  DEFAULT_REPO_NAME
  GITHUB_USER_EMAIL_MAP_JSON
  LLM_MODEL_ID
)

source .env

langgraph_url="${LANGGRAPH_URL:-http://127.0.0.1:2024}"
langgraph_health_url="${langgraph_url%/}/health"
langgraph_host_port="${langgraph_url#http://}"
langgraph_host_port="${langgraph_host_port#https://}"
langgraph_host_port="${langgraph_host_port%%/*}"
langgraph_port="${langgraph_host_port##*:}"
if [[ "$langgraph_port" == "$langgraph_host_port" ]]; then
  langgraph_port="2024"
fi

missing_vars=()
for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    missing_vars+=("$var_name")
  fi
done

case "${SANDBOX_TYPE}" in
  local)
    if [[ -z "${LOCAL_SANDBOX_ROOT_DIR:-}" ]]; then
      missing_vars+=("LOCAL_SANDBOX_ROOT_DIR")
    fi
    ;;
  langsmith)
    for var_name in LANGSMITH_API_KEY_PROD LANGSMITH_TENANT_ID_PROD LANGSMITH_TRACING_PROJECT_ID_PROD; do
      if [[ -z "${!var_name:-}" ]]; then
        missing_vars+=("$var_name")
      fi
    done
    ;;
  *)
    echo "Unsupported SANDBOX_TYPE for this helper: ${SANDBOX_TYPE}"
    echo "Use 'local' for the OpenAI POC or 'langsmith' for the hosted sandbox path."
    exit 1
    ;;
esac

case "${LLM_MODEL_ID}" in
  openai:*)
    if [[ -z "${OPENAI_API_KEY:-}" ]]; then
      missing_vars+=("OPENAI_API_KEY")
    fi
    ;;
  anthropic:*)
    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
      missing_vars+=("ANTHROPIC_API_KEY")
    fi
    ;;
  *)
    if [[ -z "${OPENAI_API_KEY:-}" && -z "${ANTHROPIC_API_KEY:-}" ]]; then
      missing_vars+=("OPENAI_API_KEY or ANTHROPIC_API_KEY")
    fi
    ;;
esac

if (( ${#missing_vars[@]} > 0 )); then
  echo "Cannot start GitHub-only POC. Fill these .env values first:"
  printf ' - %s\n' "${missing_vars[@]}"
  exit 1
fi

placeholder_vars=()
if [[ "${GITHUB_APP_PRIVATE_KEY}" == *"..."* ]]; then
  placeholder_vars+=("GITHUB_APP_PRIVATE_KEY")
fi
if [[ "${ALLOWED_GITHUB_ORGS}" == "my-org" ]]; then
  placeholder_vars+=("ALLOWED_GITHUB_ORGS")
fi
if [[ "${ALLOWED_GITHUB_REPOS}" == "my-org/my-repo" ]]; then
  placeholder_vars+=("ALLOWED_GITHUB_REPOS")
fi
if [[ "${DEFAULT_REPO_OWNER}" == "my-org" ]]; then
  placeholder_vars+=("DEFAULT_REPO_OWNER")
fi
if [[ "${DEFAULT_REPO_NAME}" == "my-repo" ]]; then
  placeholder_vars+=("DEFAULT_REPO_NAME")
fi
if [[ "${GITHUB_USER_EMAIL_MAP_JSON}" == '{"octocat":"octocat@example.com"}' ]]; then
  placeholder_vars+=("GITHUB_USER_EMAIL_MAP_JSON")
fi

if (( ${#placeholder_vars[@]} > 0 )); then
  echo "Cannot start GitHub-only POC. Replace placeholder .env values first:"
  printf ' - %s\n' "${placeholder_vars[@]}"
  exit 1
fi

if [[ "${SANDBOX_TYPE}" == "local" ]]; then
  mkdir -p "${LOCAL_SANDBOX_ROOT_DIR}"
  if [[ -z "${LANGSMITH_API_KEY_PROD:-}" && -z "${LANGSMITH_TENANT_ID_PROD:-}" && -z "${LANGSMITH_TRACING_PROJECT_ID_PROD:-}" ]]; then
    export LANGCHAIN_TRACING_V2="false"
    export PYTHONWARNINGS="${PYTHONWARNINGS:+${PYTHONWARNINGS},}ignore:API key must be provided when using hosted LangSmith API"
    echo "LangSmith tracing is disabled for this local POC because no LangSmith credentials are configured."
  fi
fi

echo "GitHub-only POC config looks complete."
echo "Next steps:"
echo "  1. In another terminal, run: ngrok http 2024"
echo "  2. Point your GitHub App webhook to: https://<your-ngrok-domain>/webhooks/github"
echo "  3. Trigger with @openswe in the allowed repo."
if [[ "${SANDBOX_TYPE}" == "local" ]]; then
  echo "  4. The repo under test will be cloned into: ${LOCAL_SANDBOX_ROOT_DIR}"
  echo
  echo "Warning: local sandbox runs commands directly on this machine."
fi
echo

if curl -fsS --max-time 2 "${langgraph_health_url}" >/dev/null 2>&1; then
  echo "LangGraph dev server is already running at ${langgraph_url}."
  echo "Reusing the existing local server instead of starting a duplicate."
  exit 0
fi

if lsof -nP -iTCP:"${langgraph_port}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port ${langgraph_port} is already in use, but ${langgraph_health_url} did not respond."
  echo "Stop the existing process or update LANGGRAPH_URL before starting the GitHub-only POC."
  exit 1
fi

echo "Starting LangGraph dev server..."
exec uv run langgraph dev --no-browser
