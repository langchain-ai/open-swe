#!/bin/sh
mkdir -p /logs/verifier
if [ "$(tr -s '[:space:]' ' ' < /app/answer.txt 2>/dev/null | sed 's/^ //; s/ $//')" = "billing_17 13" ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
