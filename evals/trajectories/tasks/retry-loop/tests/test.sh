#!/bin/sh
# Passes with the thread that opened #4812, found with at most 3 searches: the search
# always times out, so a run that keeps retrying it, from the shell or from code mode,
# fails here even when it gets the answer in the end.
mkdir -p /logs/verifier
searches=$(grep -c '"search"' /var/lib/threadctl/calls.log 2>/dev/null)
searches=${searches:-0}
echo "searches: $searches" > /logs/verifier/searches.txt
if [ "$(tr -d '[:space:]' < /app/answer.txt 2>/dev/null)" = "thr_3c7e2f90" ] \
    && [ "$searches" -le 3 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
