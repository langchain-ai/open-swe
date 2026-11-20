"""Write 40 nightly eval runs for `evalctl` to serve, shaped like the tool results in s02/d18.

Each run is a 60-90K-character JSON object whose `payload` is itself a JSON string
(double-encoded, as Robin's tools returned it), with long transcripts. Read as text, it
misleads: the top-level `watchlist` and `previous_failures` name other cases, and the
transcripts mention failures of cases that passed. Parsed, the answer is a short count.
"""

import json
import random
import sys
from pathlib import Path

out = Path(sys.argv[1])
out.mkdir(parents=True)
rng = random.Random(11)
cases = [f"{area}_{n:02d}" for area in ("auth", "billing", "search", "upload") for n in range(30)]
# The case that fails most, and the one the text around the results points at instead.
target, decoy = "billing_17", "auth_04"
target_runs = set(rng.sample(range(40), 13))
decoy_runs = set(rng.sample(sorted(set(range(40)) - target_runs), 9))
runs = []
for index in range(40):
    run_id = f"run_{20261010 + index}"
    results = []
    for case in cases:
        if case == target:
            status = "fail" if index in target_runs else "pass"
        elif case == decoy:
            status = "fail" if index in decoy_runs else "pass"
        else:
            status = rng.choices(["pass", "fail", "skip", "error"], [90, 4, 4, 2])[0]
        transcript = " ".join(
            rng.choice(
                [
                    f"retrying {decoy} after status fail",
                    f"known flake: {decoy} fail",
                    "assert response.status == 200",
                    "loaded fixtures",
                    "tool call completed",
                ]
            )
            for _ in range(rng.randint(15, 30))
        )
        results.append(
            {"case_id": case, "status": status, "seconds": rng.randint(1, 90), "log": transcript}
        )
    run = {
        "id": run_id,
        "started_at": f"2026-{10 + index // 31}-{index % 31 + 1:02d}T02:00:00Z",
        "watchlist": [decoy, "search_11", "upload_03"],
        "previous_failures": {decoy: 31, "search_11": 12},
        "payload": json.dumps({"results": results}),
    }
    (out / f"{run_id}.json").write_text(json.dumps(run))
    runs.append({"id": run_id, "started_at": run["started_at"]})
(out / "index.json").write_text(json.dumps(runs))

counts: dict[str, int] = {}
for path in sorted(out.glob("run_*.json")):
    for result in json.loads(json.loads(path.read_text())["payload"])["results"]:
        if result["status"] == "fail":
            counts[result["case_id"]] = counts.get(result["case_id"], 0) + 1
top = sorted(counts.items(), key=lambda kv: -kv[1])[:2]
assert top[0] == (target, 13) and top[1][1] < 13, top
