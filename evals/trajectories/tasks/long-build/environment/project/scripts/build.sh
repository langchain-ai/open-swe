#!/bin/sh
# Stands in for a slow compile: about six minutes, with progress, then a build marker
# the tests require.
set -eu
mkdir -p build
for step in $(seq 1 40); do
    echo "compiling module ${step}/40"
    sleep 9
done
touch build/ok
echo "build finished"
