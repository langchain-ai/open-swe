#!/bin/sh
# Stands in for Robin's background tool and its wakeup: `ci watch` runs under a job started
# from /tmp/open-swe-background-tasks, as that tool runs it, and the answer follows its end.
mkdir -p /tmp/open-swe-background-tasks
python3 - /tmp/open-swe-background-tasks/background_task.py <<'PY' | awk '/^run /{print $2}' > /workspace/answer.txt
import subprocess
subprocess.run(["ci", "watch"], check=True)
PY
