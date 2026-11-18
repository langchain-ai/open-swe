#!/usr/bin/env bash
# Local dev against the mock harness, with a REAL LLM and a Vite HMR dev server.
# The browser uses a SINGLE origin ($UI, the Vite HMR server): your ui/src edits
# hot-reload, and Vite proxies the API + Yjs collaboration WebSocket + mock
# Slack/GitHub + sign-in to the harness ($API). Same-origin is what lets the
# session cookie ride the plan-review WebSocket (cross-origin WS drops it).
# The LLM is real when E2E_REAL_LLM=1, otherwise the scripted fake;
# only the Slack/GitHub SaaS boundaries are always faked.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

API_PORT="${E2E_PORT:-2024}"
UI_PORT="${UI_PORT:-3000}"
API="http://127.0.0.1:${API_PORT}"
UI="http://127.0.0.1:${UI_PORT}"

REAL_LLM="${E2E_REAL_LLM:-}"

# Single origin = the Vite HMR server ($UI). Agent links, the sign-in redirect,
# and the same-origin/WS allowlist all point there; the UI calls the API on its
# own origin and Vite proxies it (and the WS) to the harness.
export DASHBOARD_BASE_URL="$UI"
export DASHBOARD_ALLOWED_ORIGINS="$UI"

[ -d ui/node_modules ] || pnpm install --filter open-swe-dashboard...

echo
echo "┌──────────────────────────────────────────────────────────────────────┐"
echo "│  START HERE →  ${UI}/login                            │"
echo "│  Continue with LangSmith → pick Alice or Bob (mock sign-in)           │"
echo "└──────────────────────────────────────────────────────────────────────┘"
echo "  Mock Slack:  ${UI}/mock/slack          (send as Alice or Bob)"
echo "  Everything is on ${UI} (Vite HMR; API + WS proxied to the harness)."
echo "  Slack/GitHub are mocked. Open two browser profiles to be"
echo "  two users (Alice owns the plan; Bob can comment + request changes)."
echo

# Every Open SWE thread runs on the Managed Deep Agents server beside it.
E2E_REAL_LLM="${REAL_LLM}" tests/e2e/mda-dev.sh &
MDA=$!
E2E_REAL_LLM="${REAL_LLM}" uv run --extra dev langgraph dev --config tests/e2e/langgraph.e2e.json \
  --port "${API_PORT}" --no-browser --allow-blocking --no-reload &
HARNESS=$!
trap 'kill "${HARNESS}" "${MDA}" 2>/dev/null || true' EXIT INT TERM

# E2E_HARNESS activates the dev-only proxy plugin in ui/vite.config.ts; an empty
# API base means the UI calls its own origin (proxied), keeping it same-origin.
cd ui
exec env E2E_HARNESS="${API}" VITE_DASHBOARD_API_BASE_URL="" \
  ./node_modules/.bin/vite dev --host 127.0.0.1 --port "${UI_PORT}"
