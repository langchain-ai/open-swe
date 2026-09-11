"""One-time rehearsal: preserved Store, real startup, HTTP, queue, and restart."""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4


async def run(deployment, thread_id, **state):
    return await deployment.json(
        "POST",
        f"/threads/{thread_id}/runs/wait",
        json={"assistant_id": "probe", "input": state},
    )


async def merge(deployment, number, opened_at, *, redeliver=False):
    now = datetime.now(UTC).isoformat()
    payload = {
        "action": "closed",
        "repository": {"owner": {"login": "cutover-org"}, "name": "fixture"},
        "pull_request": {
            "number": number,
            "merged": True,
            "state": "closed",
            "created_at": opened_at,
            "updated_at": now,
            "merged_at": now,
            "additions": 4,
            "deletions": 0,
            "changed_files": 1,
        },
    }
    body = json.dumps(payload).encode()
    signature = hmac.new(deployment.webhook_key.encode(), body, hashlib.sha256).hexdigest()
    headers = {
        "content-type": "application/json",
        "x-github-event": "pull_request",
        "x-github-delivery": str(uuid4()),
        "x-hub-signature-256": f"sha256={signature}",
    }
    for _ in range(2 if redeliver else 1):
        await deployment.json("POST", "/webhooks/github", content=body, headers=headers)


async def test_preserved_store_cutover_and_real_worker_restart(deployment):
    await deployment.start("legacy")
    old_time = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    old_ms = int((datetime.now(UTC) - timedelta(days=1)).timestamp() * 1000)
    records = [
        (
            ["usage", "v2", "agent_runs"],
            f"run:{i}",
            {
                "invocation_id": f"legacy-{i}",
                "run_id": f"legacy-{i}",
                "thread_id": f"legacy-thread-{i}",
                "github_login": "cutover-reader",
                "source": "dashboard",
                "model_id": "legacy-model",
                "created_at_ms": old_ms,
                "finished_at_ms": old_ms + 100,
                "total_tokens": 1000,
                "cost_usd": 10,
            },
        )
        for i in range(20)
    ]
    records.extend(
        [
            (
                ["agent_usage", "threads"],
                "older-thread",
                {
                    "thread_id": "older-thread",
                    "github_login": "cutover-reader",
                    "source": "dashboard",
                    "created_at_ms": old_ms,
                    "total_tokens": 2000,
                    "cost_usd": 20,
                },
            ),
            (["user_preferences"], "cutover-reader", {"theme": "dark"}),
            (["team_settings"], "default", {"default_repo": "cutover-org/fixture"}),
        ]
    )
    for namespace, key, value in records:
        await deployment.json(
            "PUT", "/store/items", json={"namespace": namespace, "key": key, "value": value}
        )
    thread = await deployment.json("POST", "/threads", json={})
    thread_id = thread["thread_id"]
    before = await run(deployment, thread_id, action="operational")
    assert before["counter"] == 1
    assert before["preference"] == "dark"
    scheduled = await deployment.json(
        "POST",
        f"/threads/{thread_id}/runs",
        json={"assistant_id": "probe", "input": {"action": "operational"}, "after_seconds": 20},
    )
    assert scheduled["status"] == "pending"
    await deployment.stop()

    await deployment.start("new")
    ready = await deployment.readiness()
    assert ready["ready"] is True
    cutover = ready["reporting_cutover_at"]
    assert cutover
    assert ready["dead_letters"] == 0
    report = await deployment.report()
    assert report["rows"] == []
    assert report["total_members"] == 0
    for namespace, key, value in records:
        stored = await deployment.json(
            "GET", "/store/items", params={"namespace": ".".join(namespace), "key": key}
        )
        assert stored["value"] == value
    resumed = await deployment.wait_for(
        lambda: deployment.json("GET", f"/threads/{thread_id}/state"),
        lambda state: state["values"].get("counter") == 2,
    )
    assert resumed["values"]["preference"] == "dark"

    # A Store-only invocation completing after cutover is deliberately excluded.
    crossing = await run(
        deployment,
        thread_id,
        action="finish_legacy",
        invocation_id="legacy-0",
        pr_number=1,
        opened_at=old_time,
    )
    assert crossing["completion_recorded"] is False
    await merge(deployment, 1, old_time)
    await deployment.wait_for(deployment.report, lambda data: not data["has_pending_events"])
    assert (await deployment.report())["rows"] == []
    outcomes = await deployment.json(
        "GET", "/dashboard/api/analytics/pr-merge-rate-by-model?period=all"
    )
    assert outcomes["cohorts"] == []

    # Leave real outbox events queued, then let a new process deliver them.
    await deployment.json("POST", "/cutover-fixture/pause-worker")
    new_thread = (await deployment.json("POST", "/threads", json={}))["thread_id"]
    opened_at = datetime.now(UTC).isoformat()
    new_run = await run(
        deployment,
        new_thread,
        action="new",
        invocation_id="new-run",
        pr_number=2,
        opened_at=opened_at,
    )
    assert new_run["completion_recorded"] is True
    queued = await deployment.report()
    assert queued["has_pending_events"] is True
    assert queued["rows"] == []
    await deployment.stop()
    await deployment.start("new")
    delivered = await deployment.wait_for(
        deployment.report,
        lambda data: not data["has_pending_events"] and len(data["rows"]) == 1,
    )
    row = delivered["rows"][0]
    assert row["invocations"] == 1
    assert row["total_tokens"] == 30
    assert row["total_cost_usd"] == 0.25
    assert row["invocations_without_cost"] == 0
    assert row["prs_opened"] == 1
    assert row["merged_prs"] == 0
    assert delivered["reporting_cutover_at"] == cutover

    await merge(deployment, 2, opened_at, redeliver=True)
    merged = await deployment.wait_for(
        deployment.report,
        lambda data: not data["has_pending_events"] and data["rows"][0]["merged_prs"] == 1,
    )
    outcomes = await deployment.json(
        "GET", "/dashboard/api/analytics/pr-merge-rate-by-model?period=all"
    )
    assert outcomes["status"] == "ready"
    assert len(outcomes["cohorts"]) == 1
    assert outcomes["cohorts"][0]["merged"] == 1
    assert outcomes["cohorts"][0]["cohort_size"] == 1
    assert outcomes["cohorts"][0]["model_id"] == "synthetic-model"
    assert merged["rows"][0]["invocations"] == 1

    await deployment.stop()
    await deployment.start("new")
    persisted = await deployment.report()
    assert persisted["rows"] == merged["rows"]
    assert persisted["reporting_cutover_at"] == cutover
    ready = await deployment.readiness()
    assert ready["ready"] is True
    assert ready["dead_letters"] == 0
    operational = await run(deployment, thread_id, action="operational")
    assert operational["preference"] == "dark"
    assert operational["counter"] == 4
