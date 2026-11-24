#!/bin/sh
# Stands in for Robin's background tool and its wakeup: the export runs under a job started
# from /tmp/open-swe-background-tasks, as that tool runs it, and nothing polls it.
mkdir -p /tmp/open-swe-background-tasks
python3 - /tmp/open-swe-background-tasks/background_task.py <<'PY' | awk '{print $3}' > /workspace/rows.txt
import subprocess
subprocess.run(["export-data", "--all"], check=True)
PY
