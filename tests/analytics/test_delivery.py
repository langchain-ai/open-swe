"""Capture, delivery, and transactional progress regressions."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from agent.analytics import identity, ingestion, outbox, retention
from agent.database import analytics as database
from tests.analytics.helpers import run_event


async def test_capture_start_survives_delivery_retention_and_restart(deployment_db, monkeypatch):
    monkeypatch.setenv("ANALYTICS_RAW_EVENT_MONTHS", "1")
    await database.migrate()
    person = identity.opaque_person("github", 123)
    before = datetime.now(UTC)
    events = [run_event(occurred_at=before - timedelta(days=60), person=person) for _ in range(2)]
    assert await asyncio.gather(*(outbox.enqueue(event) for event in events)) == [True, True]
    async with database.connection() as conn:
        started_at = await database.collection_started_at(conn)
    assert started_at is not None
    assert before <= started_at <= datetime.now(UTC)
    assert await outbox.deliver_batch() == 2
    assert not await outbox.enqueue(events[0])

    delayed = run_event(occurred_at=before - timedelta(days=60), person=person)
    assert await ingestion.ingest(delayed)
    assert not await ingestion.ingest(delayed)
    async with database.connection() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM run_projection")) == 3
        assert await database.collection_started_at(conn) == started_at
    async with database.transaction() as conn:
        await conn.execute(
            text("UPDATE outbox SET acknowledged_at = clock_timestamp() - interval '31 days'")
        )
        await conn.execute(
            text("UPDATE ingestion_receipts SET expires_at = clock_timestamp() - interval '1 day'")
        )
    await retention.enforce_retention()
    async with database.connection() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM events")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM outbox")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM ingestion_receipts")) == 0
        assert (
            await conn.scalar(text("SELECT sum(event_count) FROM additive_event_projection")) == 3
        )
    await database.close()
    await database.migrate()
    assert identity.opaque_person("github", 123) == person
    async with database.connection() as conn:
        assert await database.collection_started_at(conn) == started_at
        assert await conn.scalar(text("SELECT count(*) FROM run_projection")) == 3


async def test_failed_ingestion_does_not_start_collection(deployment_db):
    await database.migrate()
    async with database.transaction() as conn:
        await conn.execute(
            text("ALTER TABLE additive_event_projection ADD CONSTRAINT reject_event CHECK (false)")
        )
    event = run_event()
    with pytest.raises(IntegrityError):
        await ingestion.ingest(event)
    async with database.transaction() as conn:
        assert await database.collection_started_at(conn) is None
        assert await conn.scalar(text("SELECT count(*) FROM events")) == 0
        await conn.execute(
            text("ALTER TABLE additive_event_projection DROP CONSTRAINT reject_event")
        )
    assert await ingestion.ingest(event)
    async with database.connection() as conn:
        assert await database.collection_started_at(conn) is not None


async def test_failed_projection_does_not_advance_processing_progress(deployment_db, monkeypatch):
    from unittest.mock import AsyncMock

    await database.migrate()
    assert await ingestion.ingest(run_event())
    async with database.connection() as conn:
        before = await database.reporting_metadata(conn)
    monkeypatch.setattr(ingestion, "_project", AsyncMock(side_effect=RuntimeError("failed")))
    with pytest.raises(RuntimeError, match="failed"):
        await ingestion.ingest(run_event())
    async with database.connection() as conn:
        after = await database.reporting_metadata(conn)
    assert after["last_processed_at"] == before["last_processed_at"]
    async with database.connection() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM run_projection")) == 1
