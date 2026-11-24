#!/bin/sh
# Passes with the export's 48213 rows, from one export run as a background job, and with under
# a minute of shell sleep in all: a background job wakes Robin when it ends, so running the
# export in the foreground, or sleeping in a loop, only holds the turn.
mkdir -p /logs/verifier
cp /var/log/export-eval/*.log /logs/verifier/ 2>/dev/null
if python3 - <<'PY' >/logs/verifier/grade.txt 2>&1; then
import json
import re
import sys
from pathlib import Path

rows = Path("/workspace/rows.txt")
try:
    answer = re.sub(r"[,\s]", "", rows.read_text())
except OSError as err:
    sys.exit(f"no answer: {err}")
if answer != "48213":
    sys.exit(f"wrong answer: {answer!r}")
events = Path("/var/log/export-eval/events.log")
log = [json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []
starts = [e for e in log if e["event"] == "start"]
if len(starts) != 1:
    sys.exit(f"ran the export {len(starts)} times")
if not starts[0].get("background"):
    sys.exit("ran the export in the foreground, holding the turn until it ended")


def seconds(arg: str) -> float:
    unit = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    match = re.fullmatch(r"(\d+(?:\.\d*)?)([smhd]?)", arg)
    return float(match[1]) * unit.get(match[2], 1) if match else 0.0


sleeps = Path("/var/log/export-eval/sleeps.log")
slept = sum(
    seconds(arg)
    for line in (sleeps.read_text().splitlines() if sleeps.exists() else [])
    for arg in line.split()[1:]
)
if slept >= 60:
    sys.exit(f"slept {slept:.0f}s in the shell instead of waiting for the export to wake it")
print(f"answered {answer}; one export; {slept:.0f}s of shell sleep")
PY
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
