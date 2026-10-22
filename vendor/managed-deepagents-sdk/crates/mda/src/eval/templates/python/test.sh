#!/usr/bin/env bash
set -euo pipefail

mkdir -p /logs/verifier

python -m pip install -q pytest

set +e
python -m pytest /tests -q
status=$?
set -e

if [ "$status" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
  exit 1
fi
