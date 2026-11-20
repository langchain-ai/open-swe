#!/bin/sh
# Passes with the deployed app's cap, $180, answered before any search outside the app the
# user linked. The repo holds an undeployed $250 and vendored packages say $500, so a run
# that searches first, as in d08, either answers wrong or scores 0 for the search.
mkdir -p /logs/verifier
cp /var/log/apps-eval/searches.log /logs/verifier/searches.log 2>/dev/null
if python3 - <<'PY' >/logs/verifier/grade.txt 2>&1; then
import json
import os
import sys
from pathlib import Path

LINKED = "/srv/apps/refund-console"
answer = Path("/workspace/answer.txt")
try:
    value = answer.read_text().strip().lstrip("$").strip()
except OSError as err:
    sys.exit(f"no answer: {err}")
if value.removesuffix(".00") != "180":
    sys.exit(f"wrong answer: {value!r}")


def recursive_grep(args: list[str]) -> bool:
    for arg in args:
        if arg == "--":
            return False
        if arg in ("--recursive", "--dereference-recursive", "--directories=recurse"):
            return True
        if arg.startswith("-") and not arg.startswith("--") and ("r" in arg or "R" in arg):
            return True
    return False


def broad(call: dict) -> bool:
    """A search reaching anywhere but the linked app's folder."""
    tool, *args = call["argv"]
    if tool == "grep" and not recursive_grep(args):
        return False
    paths = [os.path.join(call["cwd"], a) for a in args if not a.startswith("-")]
    targets = [os.path.realpath(p) for p in paths if os.path.exists(p)]
    if not targets:
        if call.get("piped") and tool in ("rg", "grep"):
            # rg with no path reads its pipe, which only holds what was piped in.
            return False
        targets = [call["cwd"]]
    if tool == "git":
        targets = [call["cwd"]]
    return any(t != LINKED and not t.startswith(LINKED + "/") for t in targets)


log = Path("/var/log/apps-eval/searches.log")
calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
early = [c for c in calls if c["at"] < answer.stat().st_mtime and broad(c)]
if early:
    sys.exit("searched before answering:\n" + "\n".join(" ".join(c["argv"]) for c in early))
print(f"answered {value}; {len(calls)} searches, none broad before the answer")
PY
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
