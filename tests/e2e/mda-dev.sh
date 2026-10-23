#!/usr/bin/env bash
# The Managed Deep Agents runtime the E2E backend runs every Open SWE thread
# on: the real mda/ project, compiled by `mda build` and served by `langgraph dev`
# as `mda dev` serves it. Two things differ: the model is the scripted fake
# (mda_entrypoint.py), and run-completion webhooks may target the loopback
# harness, which `mda dev` refuses.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
E2E="$ROOT/tests/e2e"
BUILD="$E2E/.mda-build"
PORT="${E2E_MDA_PORT:-2025}"

make -C "$ROOT" --no-print-directory mda-cli
(cd "$ROOT/mda" && uv run --locked mda build --out "$BUILD")

# The generated config, serving mda_entrypoint.py instead of the generated entry.
# langgraph-api is pinned to the version the build targets, as `mda dev` does.
API_VERSION="$(
  python3 - "$BUILD" <<'EOF'
import json
import sys
from pathlib import Path

build = Path(sys.argv[1])
config = json.loads((build / "langgraph.json").read_text())
config.pop("env", None)
config["graphs"] = {graph: "../mda_entrypoint.py:agent" for graph in config["graphs"]}
config["webhooks"] = {"url": {"disable_loopback": False, "require_https": False}}
(build / "langgraph.e2e.json").write_text(json.dumps(config, indent=2))
print(config["_INTERNAL_docker_tag"].split("-py")[0])
EOF
)"

cd "$BUILD"
exec env MDA_LOCAL_DEV=1 \
  OPEN_SWE_BACKEND_URL="${E2E_BASE:-http://127.0.0.1:${E2E_PORT:-2024}}" \
  LANGSMITH_TRACING=false \
  uv run --project "$ROOT/mda" --locked \
  --with "langgraph-cli[inmem]" --with "langgraph-api==${API_VERSION}" \
  langgraph dev --config langgraph.e2e.json --port "$PORT" \
  --no-browser --no-reload --allow-blocking
