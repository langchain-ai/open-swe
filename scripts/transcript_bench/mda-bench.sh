#!/usr/bin/env bash
# tests/e2e/mda-dev.sh, but on the mda/ env's `bench` group (our worktrees) and
# with DEEPAGENTS_MESSAGES_CHANNEL passed through.
set -euo pipefail
ROOT=/Users/cwlbraa/src/open-swe-transcript
E2E="$ROOT/tests/e2e"
BUILD="$E2E/.mda-build"
PORT="${E2E_MDA_PORT:-2025}"
(cd "$ROOT/mda" && uv run --group bench mda build --out "$BUILD")
python3 - "$BUILD" <<'PY'
import json, sys
from pathlib import Path
build = Path(sys.argv[1])
config = json.loads((build / "langgraph.json").read_text())
config.pop("env", None)
config["graphs"] = {g: "../mda_entrypoint.py:agent" for g in config["graphs"]}
config["webhooks"] = {"url": {"disable_loopback": False, "require_https": False}}
(build / "langgraph.e2e.json").write_text(json.dumps(config, indent=2))
PY
cd "$BUILD"
exec env MDA_LOCAL_DEV=1 \
  OPEN_SWE_BACKEND_URL="http://127.0.0.1:${E2E_PORT:-2024}" \
  LANGSMITH_TRACING=false \
  uv run --project "$ROOT/mda" --group bench \
  langgraph dev --config langgraph.e2e.json --port "$PORT" \
  --no-browser --no-reload --allow-blocking
