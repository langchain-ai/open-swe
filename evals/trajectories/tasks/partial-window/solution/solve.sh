#!/bin/sh
# Count from the launch, a day at a time, and report the window.
python3 - <<'PY'
import json, subprocess
from datetime import datetime, timedelta
start = datetime.fromisoformat(json.loads(subprocess.check_output(["routing", "experiment", "show"]))["launched_at"])
a = b = 0
for day in range(3):
    since, until = start + timedelta(days=day), start + timedelta(days=day + 1)
    counts = json.loads(subprocess.check_output(["routing", "threads", "--since", since.isoformat(), "--until", until.isoformat()]))
    a, b = a + counts["arm_a"], b + counts["arm_b"]
json.dump({"arm_a": a, "arm_b": b, "since": start.isoformat()}, open("/workspace/answer.json", "w"))
PY
