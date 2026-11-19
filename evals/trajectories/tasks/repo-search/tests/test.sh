#!/bin/sh
mkdir -p /logs/verifier
if head -1 /app/answer.txt 2>/dev/null | grep -q 'src/routing/checkout.ts' \
    && sed -n 2p /app/answer.txt | grep -q '/checkout/v2'; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
