#!/bin/sh
# The client's default comes from settings.py; nothing needs pulling or installing to read it.
set -eu
grep -n 'REQUEST_TIMEOUT_SECONDS' /workspace/widgets/src/widgets/http/client.py
sed -n 's/^REQUEST_TIMEOUT_SECONDS = \(.*\)$/\1/p' /workspace/widgets/src/widgets/settings.py \
    > /workspace/answer.txt
