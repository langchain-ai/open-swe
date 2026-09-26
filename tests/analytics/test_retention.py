"""Retention ownership and recovery against PostgreSQL."""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.analytics import retention

type AnalyticsDB = tuple[UUID, Callable[[], AbstractAsyncContextManager[AsyncConnection]]]


async def _expired_receipt(conn: AsyncConnection, workspace: UUID) -> None:
    await conn.execute(
        text(
            "INSERT INTO ingestion_receipts "
            "(workspace_id, producer, producer_event_id, event_name, event_id, expires_at) "
            "VALUES (:workspace, 'test', :producer_id, 'test', :event_id, "
            "CURRENT_TIMESTAMP - interval '1 day')"
        ),
        {"workspace": workspace, "producer_id": uuid4().hex, "event_id": uuid4()},
    )


async def test_retention_waits_until_due_across_worker_restarts(analytics_db: AnalyticsDB) -> None:
    workspace, transaction = analytics_db
    assert await retention.enforce_retention() is True
    async with transaction() as conn:
        await _expired_receipt(conn, workspace)
    assert await retention.enforce_retention() is False
    async with transaction() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM ingestion_receipts")) == 1
        await conn.execute(
            text("UPDATE analytics_retention_schedule SET next_run_at = '-infinity'")
        )
    assert await retention.enforce_retention() is True
    async with transaction() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM ingestion_receipts")) == 0


async def test_only_one_worker_runs_retention_and_cancellation_releases_it(
    analytics_db: AnalyticsDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, transaction = analytics_db
    async with transaction() as conn:
        await _expired_receipt(conn, workspace)
    entered = asyncio.Event()
    finish = asyncio.Event()
    expire = retention._expire_records

    async def paused_expiry(conn: AsyncConnection) -> None:
        await expire(conn)
        entered.set()
        await finish.wait()

    monkeypatch.setattr(retention, "_expire_records", paused_expiry)
    first = asyncio.create_task(retention.enforce_retention())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert await asyncio.wait_for(retention.enforce_retention(), timeout=1) is False
    finally:
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
    async with transaction() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM ingestion_receipts")) == 1
    monkeypatch.setattr(retention, "_expire_records", expire)
    assert await retention.enforce_retention() is True
    async with transaction() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM ingestion_receipts")) == 0


async def test_failed_retention_rolls_back_and_remains_due(analytics_db: AnalyticsDB) -> None:
    workspace, transaction = analytics_db
    async with transaction() as conn:
        await _expired_receipt(conn, workspace)
        await conn.execute(
            text("""
            CREATE FUNCTION fail_retention() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'retention failed'; END; $$
        """)
        )
        await conn.execute(
            text("""
            CREATE TRIGGER fail_retention BEFORE DELETE ON ingestion_receipts
            FOR EACH ROW EXECUTE FUNCTION fail_retention()
        """)
        )
    with pytest.raises(DBAPIError, match="retention failed"):
        await retention.enforce_retention()
    async with transaction() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM ingestion_receipts")) == 1
        await conn.execute(text("DROP TRIGGER fail_retention ON ingestion_receipts"))
    assert await retention.enforce_retention() is True


async def test_retention_keeps_pending_outbox_and_unexpired_records(
    analytics_db: AnalyticsDB,
) -> None:
    workspace, transaction = analytics_db
    async with transaction() as conn:
        await conn.execute(
            text("""
                INSERT INTO outbox (event_id, workspace_id, event_body, state, acknowledged_at)
                VALUES (:old, :workspace, '{}', 'acknowledged', CURRENT_TIMESTAMP - interval '31 days'),
                       (:recent, :workspace, '{}', 'acknowledged', CURRENT_TIMESTAMP),
                       (:pending, :workspace, '{}', 'pending', NULL)
            """),
            {"old": uuid4(), "recent": uuid4(), "pending": uuid4(), "workspace": workspace},
        )
    await retention.enforce_retention()
    async with transaction() as conn:
        assert list(
            (await conn.execute(text("SELECT state FROM outbox ORDER BY state"))).scalars()
        ) == ["acknowledged", "pending"]
