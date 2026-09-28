"""Online analytics indexes recover from interruption without blocking ingestion."""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.database import analytics_indexes, postgres

Database = tuple[UUID, Callable[[], AbstractAsyncContextManager[AsyncConnection]]]


async def _insert(
    conn: AsyncConnection, table: Literal["outbox", "events"], workspace: UUID
) -> None:
    if table == "outbox":
        await conn.execute(
            text(
                "INSERT INTO outbox (event_id, workspace_id, event_body) VALUES (:id, :workspace, '{}')"
            ),
            {"id": uuid4(), "workspace": workspace},
        )
    else:
        await conn.execute(
            text("""
                INSERT INTO events (event_id, workspace_id, event_name, schema_version,
                    occurred_at, environment, producer, producer_event_id, entry_point,
                    privacy_classification, payload)
                VALUES (:id, :workspace, 'run.started', 1, clock_timestamp(), 'test',
                    'test', :producer_id, 'unknown', 'non_personal', '{}')
            """),
            {"id": uuid4(), "workspace": workspace, "producer_id": str(uuid4())},
        )


async def test_partition_indexes_attach_at_every_level_and_remain_idempotent(
    analytics_db: Database,
) -> None:
    _, transaction = analytics_db
    async with transaction() as conn:
        await conn.execute(
            text("""
            CREATE TABLE events_2026 PARTITION OF events
            FOR VALUES FROM ('2026-01-01') TO ('2027-01-01') PARTITION BY RANGE (occurred_at)
        """)
        )
        await conn.execute(
            text("""
            CREATE TABLE events_september PARTITION OF events_2026
            FOR VALUES FROM ('2026-09-01') TO ('2026-10-01')
        """)
        )
        await conn.execute(text("CREATE TABLE events_2026_other PARTITION OF events_2026 DEFAULT"))
    await analytics_indexes.ensure_indexes()
    async with transaction() as conn:
        for root in ("events_occurred_at_idx", "events_workspace_recorded_idx"):
            rows = (
                (
                    await conn.execute(
                        text("""
                WITH RECURSIVE indexes AS (
                    SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE c.relname = :root AND n.nspname = :schema
                    UNION ALL
                    SELECT h.inhrelid FROM pg_inherits h JOIN indexes p ON h.inhparent = p.oid
                )
                SELECT i.indisvalid FROM indexes p JOIN pg_index i ON i.indexrelid = p.oid
            """),
                        {"root": root, "schema": postgres.SCHEMA},
                    )
                )
                .scalars()
                .all()
            )
            assert rows == [True] * 5
        await conn.execute(
            text("""
            CREATE TABLE events_2027 PARTITION OF events
            FOR VALUES FROM ('2027-01-01') TO ('2028-01-01')
        """)
        )
        count = await conn.scalar(
            text("SELECT count(*) FROM pg_indexes WHERE schemaname = :schema"),
            {"schema": postgres.SCHEMA},
        )
    await asyncio.gather(analytics_indexes.ensure_indexes(), analytics_indexes.ensure_indexes())
    async with transaction() as conn:
        assert (
            await conn.scalar(
                text("SELECT count(*) FROM pg_indexes WHERE schemaname = :schema"),
                {"schema": postgres.SCHEMA},
            )
            == count
        )
        assert (
            await conn.scalar(
                text("""
            SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = :schema AND NOT i.indisvalid
        """),
                {"schema": postgres.SCHEMA},
            )
            == 0
        )


@pytest.mark.parametrize("table", ["outbox", "events"])
async def test_interrupted_build_allows_writes_and_recovers(
    analytics_db: Database, table: Literal["outbox", "events"]
) -> None:
    workspace, transaction = analytics_db
    if table == "events":
        async with transaction() as conn:
            await conn.execute(
                text(
                    "CREATE INDEX outbox_acknowledged_at_idx ON outbox "
                    "(acknowledged_at) WHERE state = 'acknowledged'"
                )
            )
            await conn.execute(
                text("CREATE INDEX events_occurred_at_idx ON ONLY events (occurred_at)")
            )
            await conn.execute(
                text(
                    "CREATE INDEX events_workspace_recorded_idx ON ONLY events "
                    "(workspace_id, recorded_at DESC)"
                )
            )
    async with transaction() as writer:
        await _insert(writer, table, workspace)
        build = asyncio.create_task(analytics_indexes.ensure_indexes())
        try:
            async with transaction() as observer:
                async with asyncio.timeout(5):
                    while not await observer.scalar(
                        text("""
                        SELECT EXISTS (
                            SELECT 1 FROM pg_locks l
                            JOIN pg_class c ON c.oid = l.relation
                            JOIN pg_namespace n ON n.oid = c.relnamespace
                            WHERE n.nspname = :schema AND c.relname = :table
                                AND l.mode = 'ShareUpdateExclusiveLock' AND l.granted
                                AND EXISTS (SELECT 1 FROM pg_index i
                                    WHERE i.indrelid = c.oid AND NOT i.indisvalid)
                        )
                    """),
                        {
                            "schema": postgres.SCHEMA,
                            "table": "events_default" if table == "events" else table,
                        },
                    ):
                        await asyncio.sleep(0.01)
            async with asyncio.timeout(5), transaction() as independent:
                await _insert(independent, table, workspace)
        finally:
            build.cancel()
            with pytest.raises(asyncio.CancelledError):
                await build
    async with transaction() as conn:
        invalid = await conn.scalar(
            text("""
            SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = :schema AND NOT i.indisvalid AND c.relkind = 'i'
        """),
            {"schema": postgres.SCHEMA},
        )
        assert invalid == 1
    async with asyncio.timeout(10):
        await analytics_indexes.ensure_indexes()
    async with transaction() as conn:
        assert (
            await conn.scalar(
                text("""
            SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = :schema AND NOT i.indisvalid
        """),
                {"schema": postgres.SCHEMA},
            )
            == 0
        )
        assert await conn.scalar(text(f"SELECT count(*) FROM {table}")) == 2


async def test_competing_fresh_initializers_do_not_hold_build_snapshots(
    analytics_db: Database,
) -> None:
    workspace, transaction = analytics_db
    tasks: list[asyncio.Task[None]] = []
    try:
        async with transaction() as writer:
            await _insert(writer, "outbox", workspace)
            tasks.append(asyncio.create_task(analytics_indexes.ensure_indexes()))
            async with transaction() as observer:
                builder_pid: int | None = None
                async with asyncio.timeout(5):
                    while builder_pid is None:
                        builder_pid = await observer.scalar(
                            text("""
                            SELECT l.pid FROM pg_locks l
                            JOIN pg_class c ON c.oid = l.relation
                            JOIN pg_namespace n ON n.oid = c.relnamespace
                            WHERE n.nspname = :schema AND c.relname = 'outbox'
                                AND l.mode = 'ShareUpdateExclusiveLock' AND l.granted
                                AND EXISTS (SELECT 1 FROM pg_index i
                                    WHERE i.indrelid = c.oid AND NOT i.indisvalid)
                        """),
                            {"schema": postgres.SCHEMA},
                        )
                        if builder_pid is None:
                            await asyncio.sleep(0.01)
                tasks.append(asyncio.create_task(analytics_indexes.ensure_indexes()))
                async with asyncio.timeout(5):
                    while True:
                        await observer.execute(text("SELECT pg_stat_clear_snapshot()"))
                        waiting = await observer.scalar(
                            text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity
                                WHERE application_name = 'open-swe-analytics-indexes'
                                  AND pid <> :builder_pid AND query LIKE '%pg%advisory_lock%')
                        """),
                            {"builder_pid": builder_pid},
                        )
                        if waiting:
                            break
                        await asyncio.sleep(0.01)
        async with asyncio.timeout(10):
            await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
