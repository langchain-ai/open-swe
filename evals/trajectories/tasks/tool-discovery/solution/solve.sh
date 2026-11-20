#!/bin/sh
# docs/data.md points at the warehouse, which has finance's ARR table.
set -eu
warehouse query "SELECT arr_usd FROM finance_arr_daily
    WHERE date = '2026-11-01' AND plan = 'enterprise'" | tail -1 > /app/answer.txt
