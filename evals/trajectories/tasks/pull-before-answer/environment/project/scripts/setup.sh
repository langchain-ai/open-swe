#!/bin/sh
# Installs the dev toolchain and warms the build caches. A question about the code never
# needs it; the eval logs each run so the grader can tell.
/usr/local/lib/widgets-eval/log setup "make setup"
for step in $(seq 1 15); do
    echo "warming cache ${step}/15"
    sleep 10
done
