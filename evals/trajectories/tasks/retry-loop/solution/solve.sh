#!/bin/sh
# The PR's commit names who asked for it; their own thread list is fast.
set -eu
cd /app
author=$(git log --grep='#4812' --format='%(trailers:key=Co-authored-by,valueonly)' \
    | sed -n 's/.*<\(.*\)>.*/\1/p' | head -1)
threadctl list --user "$author" | python3 -c '
import json, sys
threads = json.load(sys.stdin)
print(next(t["id"] for t in threads if any(p.endswith("/4812") for p in t["opened_prs"])))
' > /app/answer.txt
