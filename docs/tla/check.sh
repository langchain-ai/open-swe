#!/usr/bin/env bash
# Usage: TLA2TOOLS=/path/to/tla2tools.jar ./check.sh FALSE|TRUE
set -u
fix=${1:-FALSE}
cd "$(dirname "$0")"
logs=../../logs/tla
mkdir -p "$logs"
for inv in VisibleWhenAnswered NeverRejected ShownInSendOrder AnsweredInOrder NoStuckTurn AllDelivered; do
  for live in FALSE TRUE; do
    cfg="$logs/$fix-$inv-$live.cfg"
    printf 'CONSTANTS Fix = %s PrevRunLive = %s MaxRun = 4 MaxTurn = 4 MaxCalls = 2\nSPECIFICATION Spec\nINVARIANT %s\n' "$fix" "$live" "$inv" > "$cfg"
    log="$logs/$fix-$inv-$live.log"
    java -XX:+UseParallelGC -cp "$TLA2TOOLS" tlc2.TLC -deadlock -noGenerateSpecTE -workers auto -metadir "$logs/states" -config "$cfg" SteerRace.tla > "$log" 2>&1
    echo "fix=$fix prevRunLive=$live $inv: $(grep -E 'is violated|No error has been found|^Error:' "$log" | head -1) [$(grep -oE '[0-9]+ distinct states found' "$log" | tail -1)]"
  done
done
