#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export AI_PUBLISH_ALLOW_FORK=1
python local_fix_agent.py --last --publish --publish-pr "$@"
