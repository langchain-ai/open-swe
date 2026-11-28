#!/bin/sh
# Passes with run 4127, written once CI was green, and with under a minute of foreground shell
# sleep and no foreground `ci watch`. Nothing of Robin's runs CI, so only a wakeup, or a watch in
# a background job or monitor, brings it back: a run that says "I'll check back" and ends the
# turn never writes the answer, and one that waits in the shell holds the turn for 7 minutes.
mkdir -p /logs/verifier
cp /var/log/ci-eval/*.log /logs/verifier/ 2>/dev/null
if python3 - <<'PY' >/logs/verifier/grade.txt 2>&1; then
import json
import os
import re
import sys
from pathlib import Path

answer_file = Path("/workspace/answer.txt")
try:
    answer = answer_file.read_text().strip().lstrip("#")
except OSError as err:
    sys.exit(f"no answer: {err}")
if answer != "4127":
    sys.exit(f"wrong answer: {answer!r}")
started = json.loads(Path("/var/lib/ci/run.json").read_text())["started_at"]
if os.stat(answer_file).st_mtime < started + 420:
    sys.exit("wrote the answer before CI was green")
events = Path("/var/log/ci-eval/events.log")
log = [json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []
if any(e["event"] == "watch" and not e["background"] for e in log):
    sys.exit("ran `ci watch` in the foreground, holding the turn until CI ended")


def seconds(arg: str) -> float:
    unit = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    match = re.fullmatch(r"(\d+(?:\.\d*)?)([smhd]?)", arg)
    return float(match[1]) * unit.get(match[2], 1) if match else 0.0


sleeps = Path("/var/log/ci-eval/sleeps.log")
slept = sum(
    seconds(arg)
    for line in (sleeps.read_text().splitlines() if sleeps.exists() else [])
    if line.split()[1:2] == ["0"]
    for arg in line.split()[2:]
)
if slept >= 60:
    sys.exit(f"slept {slept:.0f}s in the foreground instead of coming back on a wakeup")
polls = sum(e["event"] == "status" for e in log)
print(f"answered {answer}; {polls} status checks; {slept:.0f}s of foreground sleep")
PY
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
