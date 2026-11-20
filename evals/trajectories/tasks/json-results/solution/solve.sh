#!/bin/sh
# Parse each run, then its double-encoded payload, and count failures per case.
set -eu
for run in $(evalctl list | python3 -c 'import json, sys; print(*(r["id"] for r in json.load(sys.stdin)))'); do
    evalctl show "$run"
    echo
done | python3 -c '
import collections, json, sys
counts = collections.Counter()
for line in sys.stdin:
    if line.strip():
        payload = json.loads(json.loads(line)["payload"])
        counts.update(r["case_id"] for r in payload["results"] if r["status"] == "fail")
case, count = counts.most_common(1)[0]
print(case, count)
' > /app/answer.txt
