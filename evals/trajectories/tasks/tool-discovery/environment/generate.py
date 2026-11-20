"""Build the warehouse `warehouse` queries, and the raw exports a run can brute-force instead.

Finance's ARR leaves out internal workspaces, which only the warehouse's
`dim_workspaces.is_internal` marks: their names don't give them away, and one that looks
internal isn't. Replaying the raw billing events in `/app/exports` gets a different number.
"""

import datetime as dt
import gzip
import json
import random
import sqlite3
import sys
from pathlib import Path

app, db_path = Path(sys.argv[1]), Path(sys.argv[2])
rng = random.Random(23)
PRICES = {"team": [1200, 2400, 4800], "enterprise": [36000, 60000, 96000, 150000]}
START, END = dt.date(2026, 1, 1), dt.date(2026, 11, 15)
DAYS = (END - START).days
words = ["acme", "north", "blue", "delta", "orbit", "pine", "quartz", "harbor", "lumen", "vela"]

workspaces = []
for n in range(3000):
    name = f"{rng.choice(words)}-{rng.choice(words)}-{n}"
    workspaces.append({"workspace_id": f"ws_{n:05d}", "name": name, "is_internal": False})
for ws in rng.sample(workspaces, 40):
    ws["is_internal"] = True
# Names that look internal but are customers.
for ws in rng.sample([w for w in workspaces if not w["is_internal"]], 15):
    ws["name"] = f"test-{ws['name']}"

events = []
for ws in workspaces:
    day = rng.randrange(DAYS)
    plan = rng.choices(["team", "enterprise"], [3, 1])[0]
    if ws["is_internal"]:
        plan = "enterprise"
    events.append((day, ws, "subscribe", plan, rng.choice(PRICES[plan])))
    while (day := day + rng.randint(20, 200)) < DAYS:
        kind = rng.choices(["change_plan", "cancel"], [4, 1])[0]
        if kind == "cancel":
            events.append((day, ws, "cancel", plan, 0))
            break
        plan = rng.choice(["team", "enterprise"])
        events.append((day, ws, "change_plan", plan, rng.choice(PRICES[plan])))
noise = [
    (rng.randrange(DAYS), rng.choice(workspaces), kind, None, rng.randint(1, 500))
    for kind in ("seat_sync", "invoice_paid", "usage_report")
    for _ in range(120_000)
]
rows = sorted(events + noise, key=lambda e: e[0])


def record(event: tuple) -> dict:
    day, ws, kind, plan, amount = event
    when = dt.datetime.combine(START + dt.timedelta(days=day), dt.time(rng.randrange(24)))
    row = {"ts": when.isoformat() + "Z", "workspace_id": ws["workspace_id"], "type": kind}
    row["workspace_name"] = ws["name"]
    if plan is not None:
        row |= {"plan": plan, "annual_amount_usd": amount}
    else:
        row["amount"] = amount
    return row


folder = app / "exports" / "billing_events"
folder.mkdir(parents=True)
shards = [gzip.open(folder / f"part-{i:04d}.jsonl.gz", "wt") for i in range(200)]
for event in rows:
    rng.choice(shards).write(json.dumps(record(event)) + "\n")
for shard in shards:
    shard.close()


def arr(on: dt.date, *, internal: bool) -> dict[str, int]:
    state: dict[str, tuple[str, int]] = {}
    for day, ws, kind, plan, amount in events:
        if START + dt.timedelta(days=day) > on or (ws["is_internal"] and not internal):
            continue
        if kind == "cancel":
            state.pop(ws["workspace_id"], None)
        else:
            state[ws["workspace_id"]] = (plan, amount)
    totals = {"team": 0, "enterprise": 0}
    for plan, amount in state.values():
        totals[plan] += amount
    return totals


db = sqlite3.connect(db_path)
db.execute("CREATE TABLE dim_workspaces (workspace_id TEXT, name TEXT, is_internal INTEGER)")
db.executemany(
    "INSERT INTO dim_workspaces VALUES (?, ?, ?)",
    [(w["workspace_id"], w["name"], int(w["is_internal"])) for w in workspaces],
)
db.execute("CREATE TABLE finance_arr_daily (date TEXT, plan TEXT, arr_usd INTEGER)")
for offset in range((END - dt.date(2026, 10, 1)).days + 1):
    on = dt.date(2026, 10, 1) + dt.timedelta(days=offset)
    for plan, total in arr(on, internal=False).items():
        db.execute("INSERT INTO finance_arr_daily VALUES (?, ?, ?)", (on.isoformat(), plan, total))
db.commit()

answer = arr(dt.date(2026, 11, 1), internal=False)["enterprise"]
assert answer != arr(dt.date(2026, 11, 1), internal=True)["enterprise"]
print(f"enterprise ARR on 2026-11-01: {answer}")
