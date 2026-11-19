#!/bin/sh
cd /app
mkdir -p /logs/verifier
if SKIP_BUILD_CHECK=1 python -m pytest -q tests >/logs/verifier/pytest.txt 2>&1; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
