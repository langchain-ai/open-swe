"""The consolidated Slack tables keep the rows of the mapping tables they replace."""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from openswe.database import postgres


@pytest.fixture
async def migration_schema() -> AsyncIterator[tuple[AsyncEngine, str]]:
    uri = os.environ.get("TEST_ANALYTICS_POSTGRES_URI")
    if not uri:
        pytest.skip("TEST_ANALYTICS_POSTGRES_URI is required")
    engine = create_async_engine(uri)
    schema = f"open_swe_test_{uuid4().hex}"
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"CREATE SCHEMA {schema}"))
        for revision in ("30ab9d51b9ec", "47544c5c96d8"):
            async with engine.begin() as conn:
                migrations = postgres.load_migrations()
                await conn.run_sync(postgres.upgrade, migrations, schema, revision)
        yield engine, schema
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await engine.dispose()


async def test_upgrade_moves_mapping_rows_into_the_consolidated_tables(
    migration_schema: tuple[AsyncEngine, str],
) -> None:
    engine, schema = migration_schema
    async with engine.begin() as conn:
        await conn.execute(text(f"SET LOCAL search_path TO {schema}, public"))
        await conn.execute(
            text("""
            INSERT INTO slack_thread_mapping
                (id, channel_id, thread_ts, thread_id, detach_nonce, copied_from_store, created_at)
            VALUES
                (gen_random_uuid(), 'C1', '1.0', 'thread-1', NULL, true, '2026-01-01T00:00:00Z'),
                (gen_random_uuid(), 'C1', '2.0', NULL, 'nonce', false, '2026-01-01T00:00:00Z'),
                (gen_random_uuid(), 'C1', '3.0', 'rebound', 'stale', false, '2026-01-01T00:00:00Z')
        """)
        )
        await conn.execute(
            text("""
            INSERT INTO slack_run_mapping
                (id, channel_id, run_id, thread_ts, message_ts, trace_message_ts,
                 triggering_user_id, agent_thread_id, started_at, copied_from_store)
            VALUES (gen_random_uuid(), 'C1', 'run-1', '1.0', '1.1', '1.05', 'U1', 'thread-1',
                    '2026-01-01T00:00:01Z', true)
        """)
        )
        await conn.execute(
            text("""
            INSERT INTO slack_message_mapping
                (id, channel_id, message_ts, run_id, thread_ts, triggering_user_id, agent_thread_id)
            VALUES (gen_random_uuid(), 'C1', '1.1', 'run-1', '1.0', 'U1', 'thread-1')
        """)
        )
        before = {
            table: (
                await conn.execute(text(f"SELECT id, created_at, updated_at FROM {table}"))
            ).all()
            for table in ("slack_thread_mapping", "slack_run_mapping", "slack_message_mapping")
        }

    async with engine.begin() as conn:
        # The revision that consolidated them; a later one drops ``copied_from_store``.
        await conn.run_sync(postgres.upgrade, postgres.load_migrations(), schema, "1cdf9e884dad")

    async with engine.begin() as conn:
        await conn.execute(text(f"SET LOCAL search_path TO {schema}, public"))
        for old, new in (
            ("slack_thread_mapping", "slack_thread"),
            ("slack_run_mapping", "slack_run"),
            ("slack_message_mapping", "slack_message"),
        ):
            moved = (
                await conn.execute(text(f"SELECT id, created_at, updated_at FROM {new}"))
            ).all()
            assert sorted(moved) == sorted(before[old])
        threads = (
            await conn.execute(
                text(
                    "SELECT thread_ts, thread_id, detach_nonce, copied_from_store "
                    "FROM slack_thread ORDER BY thread_ts"
                )
            )
        ).all()
        assert [tuple(thread) for thread in threads] == [
            ("1.0", "thread-1", None, True),
            ("2.0", None, "nonce", False),
            ("3.0", "rebound", None, False),
        ]
        run = (
            await conn.execute(
                text(
                    "SELECT run_id, message_ts, trace_message_ts, started_at IS NOT NULL, "
                    "copied_from_store FROM slack_run"
                )
            )
        ).one()
        assert tuple(run) == ("run-1", "1.1", "1.05", True, True)
        message = (
            await conn.execute(
                text("SELECT run_id, thread_ts, agent_thread_id, reactions FROM slack_message")
            )
        ).one()
        assert tuple(message) == ("run-1", "1.0", "thread-1", {})
        channels = (
            await conn.execute(text("SELECT id, name, payload, fetched_at FROM slack_channel"))
        ).all()
        assert [tuple(channel) for channel in channels] == [
            ("C1", "", {}, datetime.fromtimestamp(0, UTC))
        ]
        remaining = await conn.scalar(
            text(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = :schema "
                "AND table_name LIKE 'slack\\_%\\_mapping'"
            ),
            {"schema": schema},
        )
        assert remaining == 0
