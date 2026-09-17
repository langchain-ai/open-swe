#!/bin/sh
# Usage: measure_state.sh <base-url> <thread-id> [runs]
# Times the LangGraph API reads the dashboard depends on, plus the transcript view when patched.
B=$1; T=$2; N=${3:-3}
for p in "/threads/$T" "/threads/$T/state" "/threads/$T/state?view=transcript"; do
  for i in $(seq 1 $N); do
    curl -s -o /dev/null -w "GET $p ttfb=%{time_starttransfer}s total=%{time_total}s bytes=%{size_download} status=%{http_code}\n" "$B$p"
  done
done
for l in 1 5; do
  for i in $(seq 1 $N); do
    curl -s -o /dev/null -X POST -H 'content-type: application/json' -d "{\"limit\":$l}" -w "POST /history limit=$l ttfb=%{time_starttransfer}s total=%{time_total}s bytes=%{size_download}\n" "$B/threads/$T/history"
  done
done
