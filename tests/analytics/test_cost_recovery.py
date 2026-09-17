import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from agent.analytics import cost_recovery as recovery
from agent.analytics import outbox, usage
from agent.analytics.cost_recovery_ops import operate
from agent.analytics.identity import opaque_id
from agent.utils.langsmith import LangSmithThreadCost


async def seed(transaction):
    await usage.record_agent_invocation_usage(
        invocation_id="invocation",
        thread_id="thread",
        github_login=None,
        user_email=None,
        model_id="model",
        effort=None,
        source="dashboard",
    )
    await usage.record_agent_invocation_completion(
        invocation_id="invocation", thread_id="thread", usage=None
    )
    await due(transaction)


async def due(transaction):
    async with transaction() as conn:
        await conn.execute(
            text(
                "UPDATE run_cost_refresh SET next_attempt_at = clock_timestamp() - interval '1 second'"
            )
        )
        await conn.execute(
            text(
                "UPDATE cost_recovery_dispatcher SET available_at = clock_timestamp() - interval '1 second'"
            )
        )


async def state(transaction):
    async with transaction() as conn:
        return (await conn.execute(text("SELECT * FROM run_cost_refresh"))).mappings().one()


async def test_recovery_after_fast_retries_waits_for_projection(analytics_db, monkeypatch):
    _, transaction = analytics_db
    await seed(transaction)
    now = datetime.now(UTC)
    lookup = AsyncMock(side_effect=[None] * 5 + [LangSmithThreadCost(0, now, now)])
    monkeypatch.setattr(recovery, "get_langsmith_thread_cost", lookup)
    for _ in range(6):
        job = await recovery.claim_job()
        assert job is not None
        await recovery.process_job(job)
        await due(transaction)
    assert (await state(transaction))["state"] == "awaiting_delivery"
    assert await recovery.claim_job() is None
    await recovery.maintain_jobs()
    assert (await state(transaction))["state"] == "awaiting_delivery"
    await outbox.deliver_batch()
    await recovery.maintain_jobs()
    assert (await state(transaction))["state"] == "complete"
    assert lookup.await_count == 6
    async with transaction() as conn:
        assert await conn.scalar(text("SELECT cost_usd FROM latest_cost_projection")) == 0


async def test_claim_crash_recovery_fences_late_worker(analytics_db, monkeypatch):
    _, transaction = analytics_db
    await seed(transaction)
    jobs = await asyncio.gather(recovery.claim_job(), recovery.claim_job())
    old = next(job for job in jobs if job)
    assert sum(job is not None for job in jobs) == 1
    async with transaction() as conn:
        await conn.execute(
            text(
                "UPDATE run_cost_refresh SET lease_until = clock_timestamp() - interval '1 second'"
            )
        )
    await due(transaction)
    new = await recovery.claim_job()
    assert new and old.token != new.token
    now = datetime.now(UTC)
    monkeypatch.setattr(
        recovery,
        "get_langsmith_thread_cost",
        AsyncMock(return_value=LangSmithThreadCost(2, now, now)),
    )
    await recovery.process_job(old)
    assert (await state(transaction))["cost_event_id"] is None
    await recovery.process_job(new)
    assert (await state(transaction))["state"] == "awaiting_delivery"


async def test_dead_letter_repair_exhaustion_and_retention(analytics_db, monkeypatch):
    workspace, transaction = analytics_db
    await seed(transaction)
    now = datetime.now(UTC)
    monkeypatch.setattr(
        recovery,
        "get_langsmith_thread_cost",
        AsyncMock(return_value=LangSmithThreadCost(1, now, now)),
    )
    await recovery.process_job(await recovery.claim_job())
    async with transaction() as conn:
        await conn.execute(
            text(
                "UPDATE outbox SET state = 'dead_letter' WHERE event_body ->> 'event_name' = 'run.cost_recorded'"
            )
        )
    await recovery.maintain_jobs()
    assert (await state(transaction))["error_code"] == "delivery_dead_letter"
    with pytest.raises(ValueError, match="Workspace"):
        await operate("requeue", uuid4(), opaque_id("run", "invocation"))
    await operate("requeue", workspace, opaque_id("run", "invocation"))
    assert (await state(transaction))["state"] == "awaiting_delivery"
    async with transaction() as conn:
        await conn.execute(
            text(
                "UPDATE run_cost_refresh SET retry_started_at = clock_timestamp() - interval '8 days'"
            )
        )
    await recovery.maintain_jobs()
    assert (await state(transaction))["error_code"] == "exhausted"
    async with transaction() as conn:
        await conn.execute(
            text(
                "UPDATE run_cost_refresh SET scheduled_at = clock_timestamp() - interval '31 days'"
            )
        )
    await recovery.maintain_jobs()
    assert (await state(transaction))["invocation_id"] is None
    await outbox.deliver_batch()
    await recovery.maintain_jobs()
    assert (await state(transaction))["state"] == "complete"


async def test_terminal_enqueue_rolls_back_with_job_failure(analytics_db, monkeypatch):
    _, transaction = analytics_db
    await usage.record_agent_invocation_usage(
        invocation_id="invocation",
        thread_id="thread",
        github_login=None,
        user_email=None,
        model_id="model",
        effort=None,
        source="dashboard",
    )
    monkeypatch.setattr(recovery, "enqueue_job", AsyncMock(side_effect=RuntimeError("unavailable")))
    with pytest.raises(RuntimeError):
        await usage.record_agent_invocation_completion(
            invocation_id="invocation", thread_id="thread", usage=None
        )
    async with transaction() as conn:
        assert (
            await conn.scalar(
                text(
                    "SELECT count(*) FROM outbox WHERE event_body ->> 'event_name' = 'run.completed'"
                )
            )
            == 0
        )


async def test_rate_limit_pauses_dispatcher(analytics_db, monkeypatch):
    _, transaction = analytics_db
    await seed(transaction)

    class RateLimit(Exception):
        status_code = 429

    monkeypatch.setattr(recovery, "get_langsmith_thread_cost", AsyncMock(side_effect=RateLimit()))
    await recovery.process_job(await recovery.claim_job())
    assert (await state(transaction))["error_code"] == "rate_limited"
    assert await recovery.claim_job() is None
    assert recovery.classify_failure(TimeoutError()).retryable


@pytest.mark.parametrize("revision", ["0017", "0018"])
async def test_migration_keeps_legacy_markers_unmapped(deployment_db, revision):
    from agent.database import postgres

    workspace, run = uuid4(), uuid4()
    async with postgres.engine().begin() as conn:
        await conn.execute(text("CREATE SCHEMA open_swe"))
        await conn.run_sync(postgres.upgrade, postgres.load_migrations(), "open_swe", revision)
        await conn.execute(
            text("INSERT INTO run_cost_refresh VALUES (:workspace, :run, clock_timestamp())"),
            {"workspace": workspace, "run": run},
        )
    await postgres.migrate()
    async with postgres.connection() as conn:
        row = (await conn.execute(text("SELECT * FROM run_cost_refresh"))).mappings().one()
        assert row["state"] == "legacy_unmapped"
        assert row["invocation_id"] is None
        assert row["workspace_id"] == workspace


async def test_disabled_worker_never_claims(analytics_db, monkeypatch):
    _, transaction = analytics_db
    await seed(transaction)
    monkeypatch.setenv("COST_RECOVERY_ENABLED", "false")
    stop = asyncio.Event()
    maintain = recovery.maintain_jobs

    async def maintain_and_stop():
        await maintain()
        stop.set()

    monkeypatch.setattr(recovery, "maintain_jobs", maintain_and_stop)
    await recovery.run_worker(stop)
    assert (await state(transaction))["attempts"] == 0


async def test_project_timeout_retries_without_poisoning_resolution(monkeypatch):
    from types import SimpleNamespace

    from agent.utils import langsmith

    project = str(uuid4())
    client = SimpleNamespace(
        read_project=AsyncMock(side_effect=[TimeoutError(), SimpleNamespace(id=project)])
    )
    monkeypatch.setattr(langsmith, "_build_langsmith_client", lambda: client)
    name = str(uuid4())
    with pytest.raises(TimeoutError):
        await langsmith._resolve_project_id_by_name(name, strict=True)
    assert await langsmith._resolve_project_id_by_name(name, strict=True) == project


async def test_permission_failure_requires_repair(analytics_db, monkeypatch):
    from langsmith.utils import LangSmithAuthError

    _, transaction = analytics_db
    await seed(transaction)
    monkeypatch.setattr(
        recovery, "get_langsmith_thread_cost", AsyncMock(side_effect=LangSmithAuthError("secret"))
    )
    await recovery.process_job(await recovery.claim_job())
    row = await state(transaction)
    assert row["state"] == "needs_attention"
    assert row["error_code"] == "permission"


async def test_legacy_duplicates_and_other_workspaces_are_isolated(analytics_db):
    from agent import agent_cost

    workspace, transaction = analytics_db
    await agent_cost.run_agent_cost_refresh({"thread_id": "thread", "run_id": "old", "attempt": 4})
    await agent_cost.run_agent_cost_refresh({"thread_id": "thread", "run_id": "old", "attempt": 0})
    async with transaction() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM run_cost_refresh")) == 1
        await conn.execute(
            text("UPDATE run_cost_refresh SET workspace_id = :other"), {"other": uuid4()}
        )
    await due(transaction)
    assert await recovery.claim_job() is None
    assert await operate("status", workspace) == []
