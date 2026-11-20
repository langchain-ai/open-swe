#!/bin/sh
# Passes when the code is fixed and the run in /app/answer.txt passed every job before the
# answer was written. Lint fails on the pushed code within a minute and the tests take 8,
# so a run that blocks on `ci watch` for the first run has no time left to see a rerun go
# green.
cd /app
mkdir -p /logs/verifier
if ruff check src tests >/logs/verifier/lint.txt 2>&1 \
    && python -m pytest -q tests >/logs/verifier/pytest.txt 2>&1 \
    && python3 - <<'PY' >/logs/verifier/ci.txt 2>&1
import json
import os
import sys
from pathlib import Path

answer = Path("/app/answer.txt")
number = int(answer.read_text().strip())
run = json.loads(Path(f"/var/lib/ci/run-{number:03d}.json").read_text())
passed = all(job["passed"] for job in run["jobs"].values())
# The run's last job finishes 480s after it starts.
green_when_answered = os.stat(answer).st_mtime >= run["started_at"] + 480
print(f"run {number}: passed={passed} green_when_answered={green_when_answered}")
sys.exit(0 if passed and green_when_answered else 1)
PY
then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
