#!/bin/sh
mkdir -p /logs/verifier
echo 0 > /logs/verifier/reward.txt
cp /var/log/metrics-eval/calls.log /logs/verifier/calls.log 2>/dev/null
python3 - <<'PY'
import json
from pathlib import Path

try:
    calls = [json.loads(line) for line in Path("/var/log/metrics-eval/calls.log").read_text().splitlines()]
    exports = [(call.get("argv"), call.get("completed", False)) for call in calls]
    valid = exports == [(["report", "--week", "47"], False), (["report", "--week", "47"], True)]
    valid = valid and Path("/workspace/answer.txt").read_text().strip() == "checkout"
except (OSError, ValueError) as exc:
    print(f"Missing or invalid evaluation evidence: {exc}")
    valid = False
Path("/logs/verifier/reward.txt").write_text(f"{int(valid)}\n")
PY
