#!/bin/sh
# Check early, fix the lint failure, rerun, then poll until the rerun is green.
set -eu
cd /app
ci status
sleep 50
ci logs 1 lint || true
sed -i '/^import os$/d' src/widgets/budget.py
ruff check src tests
git -c user.name=eval -c user.email=eval@example.com commit -qam 'fix: drop unused import'
run=$(ci rerun | sed -n 's/^run \([0-9]*\).*/\1/p')
until ci status "$run" | grep -q 'test   pass'; do
    if ci status "$run" | grep -q fail; then
        ci status "$run"
        exit 1
    fi
    sleep 30
done
echo "$run" > /app/answer.txt
