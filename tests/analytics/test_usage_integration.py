"""Product capture reaches PostgreSQL reports without LangGraph Store."""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.exceptions import ModelAuthenticationError
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import text

from agent import agent_cost
from agent.analytics import directory, emitter, ingestion, outbox, queries, usage
from agent.analytics.events import EventEnvelope
from agent.middleware.record_run_usage import record_run_usage
from agent.utils.langsmith import LangSmithThreadCost
from agent.utils.run_usage import RunUsageSummary
from tests.analytics.helpers import DAY


@pytest.fixture(autouse=True)
async def usage_storage(analytics_db, monkeypatch):
    _, transaction = analytics_db
    monkeypatch.setenv("POSTGRES_URI", "postgresql://localhost/analytics_test")
    for module in (directory, ingestion, outbox):
        monkeypatch.setattr(module, "transaction", transaction)
    monkeypatch.setattr(queries, "connection", transaction)
    current = [DAY]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0]

    monkeypatch.setattr(usage, "datetime", Clock)
    monkeypatch.setattr(emitter, "datetime", Clock)
    async with transaction() as conn:
        await conn.execute(
            text("UPDATE deployment_metadata SET reporting_cutover_at = :start"),
            {"start": DAY - timedelta(days=1)},
        )
    return current


async def _start(invocation_id="run"):
    await usage.record_agent_invocation_usage(
        invocation_id=invocation_id,
        thread_id="thread",
        github_login="octo",
        user_email="octo@example.com",
        github_user_id=123,
        model_id="model",
        effort=None,
        source="dashboard",
    )


async def _deliver(transaction, *, reverse=False):
    async with transaction() as conn:
        bodies = list(
            (
                await conn.execute(text("SELECT event_body FROM outbox ORDER BY created_at"))
            ).scalars()
        )
    for body in reversed(bodies) if reverse else bodies:
        await ingestion.ingest(EventEnvelope.model_validate(body))


async def _report():
    return await queries.usage_leaderboard(
        period="all",
        limit=10,
        current_login="octo",
        current_email="octo@example.com",
    )


async def test_queued_completion_and_cost_are_accounted_before_delivery(
    analytics_db, usage_storage
):
    _, transaction = analytics_db
    await _start()
    usage_storage[0] = DAY + timedelta(seconds=12)
    summary = RunUsageSummary(
        models=("model",), input_tokens=100, output_tokens=50, total_tokens=150
    )
    assert await usage.record_agent_invocation_completion(invocation_id="run", usage=summary)
    assert not await usage.record_agent_invocation_completion(invocation_id="run", usage=summary)
    assert await usage.agent_invocation_needs_cost_refresh(invocation_id="run")
    await usage.record_agent_invocation_cost(invocation_id="run", cost_usd=1.25)
    assert not await usage.agent_invocation_needs_cost_refresh(invocation_id="run")
    async with transaction() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM run_projection")) == 0
    await _deliver(transaction, reverse=True)
    await _deliver(transaction)
    report = await _report()
    row = report["rows"][0]
    assert row["invocations"] == 1
    assert row["total_tokens"] == 150
    assert row["total_cost_usd"] == 1.25
    assert row["avg_run_seconds"] == 12
    async with transaction() as conn:
        cost = (await conn.execute(text("SELECT * FROM latest_cost_projection"))).mappings().one()
        assert cost["observation_revision"] > 2**31
        assert cost["cost_usd"] == Decimal("1.25")


async def test_concurrent_duplicate_completion_and_cost_schedule(analytics_db):
    _, transaction = analytics_db
    await _start()
    assert not await usage.agent_invocation_needs_cost_refresh(invocation_id="run")
    results = await asyncio.gather(
        *(
            usage.record_agent_invocation_completion(invocation_id="run", usage=None)
            for _ in range(2)
        )
    )
    assert sorted(results) == [False, True]
    await usage.mark_agent_invocation_cost_refresh_scheduled(invocation_id="run")
    async with transaction() as conn:
        first = await conn.scalar(text("SELECT scheduled_at FROM run_cost_refresh"))
    await usage.mark_agent_invocation_cost_refresh_scheduled(invocation_id="run")
    assert not await usage.agent_invocation_needs_cost_refresh(invocation_id="run")
    async with transaction() as conn:
        assert await conn.scalar(text("SELECT scheduled_at FROM run_cost_refresh")) == first


async def test_unknown_runs_and_invalid_costs_do_not_create_accounting(analytics_db):
    _, transaction = analytics_db
    assert not await usage.record_agent_invocation_completion(invocation_id="unknown", usage=None)
    assert not await usage.agent_invocation_needs_cost_refresh(invocation_id="unknown")
    await usage.record_agent_invocation_cost(invocation_id="unknown", cost_usd=1.25)
    await usage.mark_agent_invocation_cost_refresh_scheduled(invocation_id="unknown")
    async with transaction() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM outbox")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM run_cost_refresh")) == 0
    await _start()
    for cost in (-1, float("nan"), float("inf")):
        await usage.record_agent_invocation_cost(invocation_id="run", cost_usd=cost)
    async with transaction() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM outbox")) == 1


async def test_pr_metrics_and_outcomes_survive_webhook_reordering(analytics_db, usage_storage):
    _, transaction = analytics_db
    await usage.record_agent_pr_usage(
        thread_id=None,
        github_login="octo",
        user_email="octo@example.com",
        owner="org",
        repo="repo",
        pr_number=1,
        pr_url=None,
        head="private-branch",
        base="main",
        additions=10,
        deletions=2,
        changed_files=1,
        created_at=DAY.isoformat(),
        repository_private=False,
    )
    usage_storage[0] = DAY + timedelta(days=2)
    await usage.update_agent_pr_usage_from_webhook(
        {
            "repository": {"owner": {"login": "org"}, "name": "repo"},
            "pull_request": {
                "number": 1,
                "updated_at": usage_storage[0].isoformat(),
                "additions": 30,
                "deletions": 4,
                "changed_files": 3,
            },
        }
    )
    for action, merged, day in [("closed", True, 1), ("reopened", False, 2)]:
        await usage.update_agent_pr_usage_from_webhook(
            {
                "action": action,
                "repository": {"owner": {"login": "org"}, "name": "repo"},
                "pull_request": {
                    "number": 1,
                    "updated_at": (DAY + timedelta(days=day)).isoformat(),
                    "merged": merged,
                },
            }
        )
    await _deliver(transaction, reverse=True)
    row = (await _report())["rows"][0]
    assert (row["prs_opened"], row["merged_prs"], row["additions"], row["deletions"]) == (
        1,
        0,
        30,
        4,
    )
    assert row["agent_loc"] == 34
    async with transaction() as conn:
        assert await conn.scalar(text("SELECT current_state FROM pr_projection")) == "open"
        bodies = list((await conn.execute(text("SELECT event_body::text FROM outbox"))).scalars())
        assert all("private-branch" not in body for body in bodies)


async def test_reviewer_records_unsurfaced_and_only_new_head_findings(analytics_db, usage_storage):
    _, transaction = analytics_db
    finding = {
        "id": "new",
        "status": "open",
        "first_seen_sha": "head",
        "last_confirmed_sha": "head",
        "severity": "high",
        "category": "correctness",
        "surface_state": "surfaced",
        "github_review_comment_ids": [123],
        "interactions": [{"kind": "human_reply"}, {"kind": "bot_reply"}],
    }
    hidden = {
        **finding,
        "id": "existing",
        "first_seen_sha": "old",
        "surface_state": "not_surfaced",
        "github_review_comment_ids": [],
        "interactions": [],
    }
    await usage.record_reviewer_publication(
        thread_id="review",
        owner="org",
        repo="repo",
        pr_number=1,
        head_sha="head",
        findings=[finding, hidden],
    )
    usage_storage[0] = DAY + timedelta(days=1)
    await usage.record_reviewer_finding_state(
        "review",
        {
            **finding,
            "pr": {"owner": "org", "name": "repo", "number": 1},
            "status": "resolved",
            "last_confirmed_sha": "fix",
        },
    )
    await _deliver(transaction, reverse=True)
    stats = (await _report())["reviewer_stats"]
    assert stats["findings_recorded"] == 2
    assert stats["surfaced_findings"] == 1
    assert stats["addressed_findings"] == 1
    assert stats["resolved_after_update"] == 1
    assert stats["human_replies"] == 1
    async with transaction() as conn:
        assert await conn.scalar(text("SELECT finding_count FROM review_projection")) == 1


@pytest.mark.parametrize(
    ("prior_tokens", "trace_cost"), [(0, 0.0), (100, 0.4), (0, 0.6), (0, None), (100, None)]
)
async def test_auth_failure_is_terminal_and_cost_coverage_uses_trace_evidence(
    analytics_db, usage_storage, monkeypatch, prior_tokens, trace_cost
):
    _, transaction = analytics_db
    await _start()
    usage_storage[0] = DAY + timedelta(seconds=12)
    monkeypatch.setattr(
        "agent.run_config.get_config",
        lambda: {"configurable": {"thread_id": "thread", "invocation_id": "run"}},
    )
    scheduled = AsyncMock(return_value=True)
    monkeypatch.setattr(agent_cost, "schedule_agent_cost_refresh", scheduled)
    messages = [HumanMessage(content="Current task")]
    if prior_tokens:
        messages.append(
            AIMessage(
                content="Earlier work in this invocation",
                response_metadata={"open_swe_invocation_id": "run"},
                usage_metadata={
                    "input_tokens": prior_tokens,
                    "output_tokens": 0,
                    "total_tokens": prior_tokens,
                },
            )
        )
    request = ModelRequest(model=MagicMock(), messages=messages, state={"messages": messages})
    error = ModelAuthenticationError("Authentication rejected")
    with pytest.raises(ModelAuthenticationError) as raised:
        await record_run_usage.awrap_model_call(request, AsyncMock(side_effect=error))
    assert raised.value is error
    assert scheduled.await_count == 1
    await _deliver(transaction)
    async with transaction() as conn:
        run = (await conn.execute(text("SELECT * FROM run_projection"))).mappings().one()
        assert run["technical_status"] == "failed"
        assert run["total_tokens"] == (prior_tokens or None)
        payload = await conn.scalar(
            text(
                "SELECT event_body -> 'payload' FROM outbox WHERE event_body ->> 'event_name' = 'run.failed'"
            )
        )
        assert payload["failure_code"] == "authentication_rejected"
    row = (await _report())["rows"][0]
    assert row["invocations"] == 1
    assert row["invocations_without_cost"] == 1

    snapshot = None if trace_cost is None else LangSmithThreadCost(trace_cost, DAY, DAY)
    monkeypatch.setattr(agent_cost, "get_langsmith_thread_cost", AsyncMock(return_value=snapshot))
    result = await agent_cost.run_agent_cost_refresh(
        {"thread_id": "thread", "invocation_id": "run", "attempt": 4}, client=MagicMock()
    )
    assert result["status"] == ("exhausted" if trace_cost is None else "updated")
    await _deliver(transaction)
    row = (await _report())["rows"][0]
    assert row["invocations"] == 1
    assert row["invocations_without_cost"] == int(trace_cost is None)
    assert row["total_cost_usd"] == (trace_cost or 0.0)
    assert row["total_tokens"] == prior_tokens
