#!/bin/sh
# Read the deployed app the user linked; the repo has an unreleased, higher cap.
set -eu
sed -n 's/.*const REFUND_CAP_USD = \([0-9]*\);.*/\1/p' /srv/apps/refund-console/index.html \
    > /workspace/answer.txt
