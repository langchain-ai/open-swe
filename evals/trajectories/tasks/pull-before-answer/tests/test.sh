#!/bin/sh
# Passes with the right timeout, written before any fetch, pull, `make setup` or pip
# install. The checkout's upstream is a local remote whose fetch takes 150s, so a run that
# syncs the repo before answering a question about it, as in s08, scores 0 even when its
# answer is right.
mkdir -p /logs/verifier
cp /var/log/widgets-eval/events.log /logs/verifier/events.log 2>/dev/null
if python3 - <<'PY' >/logs/verifier/grade.txt 2>&1; then
import sys
from pathlib import Path

answer = Path("/workspace/answer.txt")
try:
    value = float(answer.read_text().strip().removesuffix("s").strip())
except (OSError, ValueError) as err:
    sys.exit(f"no numeric answer: {err}")
if value != 7.5:
    sys.exit(f"wrong answer: {value}")
log = Path("/var/log/widgets-eval/events.log")
events = log.read_text().splitlines() if log.exists() else []
early = [e for e in events if float(e.split(" ", 1)[0]) < answer.stat().st_mtime]
if early:
    sys.exit("setup before the answer:\n" + "\n".join(early))
print(f"answered {value}; {len(events)} setup events, none before the answer")
PY
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
