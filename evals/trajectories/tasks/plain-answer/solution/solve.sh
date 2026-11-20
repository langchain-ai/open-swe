#!/bin/sh
# Two plain bullets from the CSV; no report, chart or artifact.
set -eu
python3 - <<'PY' > /workspace/answer.txt
import csv
from collections import defaultdict

passed, total = defaultdict(int), defaultdict(int)
with open("/workspace/evals/runs.csv") as f:
    for row in csv.DictReader(f):
        for key in (row["week"], (row["week"], row["suite"])):
            passed[key] += int(row["passed"])
            total[key] += int(row["total"])
weeks = sorted(k for k in total if isinstance(k, str))
rate = {k: 100 * passed[k] / total[k] for k in total}
suites = {s for w, s in (k for k in total if isinstance(k, tuple))}
worst = max(suites, key=lambda s: rate[(weeks[0], s)] - rate[(weeks[1], s)])
print(f"- Pass rate: {rate[weeks[0]]:.1f}% in {weeks[0]}, {rate[weeks[1]]:.1f}% in {weeks[1]}")
print(
    f"- {worst} dropped the most: "
    f"{rate[(weeks[0], worst)]:.1f}% to {rate[(weeks[1], worst)]:.1f}%"
)
PY
cat /workspace/answer.txt
