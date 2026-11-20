"""Draft: ARR from the raw billing exports. Unreviewed; doesn't match finance yet."""

import glob
import gzip
import json

state = {}
for path in sorted(glob.glob("exports/billing_events/*.jsonl.gz")):
    with gzip.open(path, "rt") as f:
        for line in f:
            event = json.loads(line)
            if event["type"] in ("subscribe", "change_plan"):
                state[event["workspace_id"]] = (event["plan"], event["annual_amount_usd"])
print(sum(amount for plan, amount in state.values() if plan == "enterprise"))
