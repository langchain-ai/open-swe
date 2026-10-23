#!/usr/bin/env bash
# Harbor verifier: run TypeScript tests with Node's built-in test runner.
set -euo pipefail

mkdir -p /logs/verifier

set +e
node --experimental-strip-types --test /tests/*.test.ts
status=$?
set -e

if [ "$status" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
  exit 1
fi
