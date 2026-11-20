#!/bin/sh
# finance_arr_daily's figure. Replaying the raw exports gives 76782000 (internal
# workspaces included) and scripts/arr_draft.py 84720000.
mkdir -p /logs/verifier
if [ "$(tr -d '[:space:],$' < /app/answer.txt 2>/dev/null)" = "75408000" ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
