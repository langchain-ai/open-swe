"""Upgrade the preview transcript schema without losing existing data."""

import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from agent.database import postgres
from agent.database.migrations.transcript_legacy import schema_definition

LEGACY_SQL = Path(__file__).with_name("fixtures") / "legacy_transcript.sql"


@pytest.fixture
async def migration_schema() -> AsyncIterator[tuple[AsyncEngine, str]]:
    uri = os.environ.get("TEST_ANALYTICS_POSTGRES_URI")
    if not uri:
        pytest.skip("TEST_ANALYTICS_POSTGRES_URI is required")
    engine = create_async_engine(uri.replace("postgresql://", "postgresql+asyncpg://"))
    schema = f"open_swe_test_{uuid4().hex}"
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"CREATE SCHEMA {schema}"))
            await conn.run_sync(postgres.upgrade, postgres.load_migrations(), schema, "0023")
        yield engine, schema
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await engine.dispose()


async def install_legacy(engine: AsyncEngine, schema: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(f"SET LOCAL search_path TO {schema}, public"))
        for statement in LEGACY_SQL.read_text().split(";"):
            if statement.strip():
                await conn.execute(text(statement))


@pytest.mark.parametrize("legacy", [False, True])
async def test_upgrade_supports_current_transcript_writes(
    migration_schema: tuple[AsyncEngine, str], legacy: bool
) -> None:
    engine, schema = migration_schema
    if legacy:
        await install_legacy(engine, schema)
    async with engine.begin() as conn:
        await conn.run_sync(postgres.upgrade, postgres.load_migrations(), schema)
        await conn.execute(text("INSERT INTO thread (thread_id) VALUES ('a'), ('b')"))
        for thread in ("a", "b"):
            params = {"thread": thread, "turn": uuid4()}
            await conn.execute(
                text("""
                INSERT INTO thread_command_receipt (thread_id, command_id, result_version)
                VALUES (:thread, 'shared', 1)
            """),
                params,
            )
            await conn.execute(
                text("""
                INSERT INTO thread_message
                    (thread_id, message_id, turn_id, version, role, attachments, usage, created_at)
                VALUES (:thread, 'shared', :turn, 1, 'ai', '[]', '{}', now())
            """),
                params,
            )
            await conn.execute(
                text("""
                INSERT INTO thread_tool_call
                    (thread_id, tool_call_id, turn_id, version, name, input, status, started_at)
                VALUES (:thread, 'shared', :turn, 1, 'tool', '{}', 'completed', now())
            """),
                params,
            )
            await conn.execute(
                text("""
                INSERT INTO thread_tool_output (thread_id, tool_call_id, output)
                VALUES (:thread, 'shared', 'result')
            """),
                params,
            )
            await conn.execute(
                text("""
                INSERT INTO thread_turn_checkpoint
                    (thread_id, turn_id, checkpoint_turn_count, checkpoint_ref, status, completed_at)
                VALUES (:thread, :turn, 1, 'checkpoint', 'ready', now())
            """),
                params,
            )
            await conn.execute(
                text("""
                INSERT INTO thread_attachment
                    (attachment_id, thread_id, message_id, position, mime_type, data)
                VALUES (:turn, :thread, 'shared', 0, 'image/png', '\\x00')
            """),
                params,
            )
        assert (await conn.execute(text("SELECT count(*) FROM thread_message"))).scalar_one() == 2
        await conn.run_sync(postgres.upgrade, postgres.load_migrations(), schema)
        assert (
            await conn.execute(text("SELECT count(*) FROM thread_tool_output"))
        ).scalar_one() == 2


@pytest.mark.parametrize(
    "change,error",
    [
        ("INSERT INTO thread (thread_id) VALUES ('keep-me')", "populated"),
        (
            "INSERT INTO thread_command_receipt (thread_id, command_id, status) VALUES ('a', 'keep-me', 'rejected')",
            "populated",
        ),
        ("ALTER TABLE thread ALTER COLUMN kind SET DEFAULT 'unknown'", "Unrecognized"),
        ("DROP TABLE thread_message", "Unrecognized"),
    ],
)
async def test_unsafe_legacy_upgrade_leaves_schema_and_version_unchanged(
    migration_schema: tuple[AsyncEngine, str], change: str, error: str
) -> None:
    engine, schema = migration_schema
    await install_legacy(engine, schema)
    async with engine.begin() as conn:
        await conn.execute(text(f"SET LOCAL search_path TO {schema}, public"))
        await conn.execute(text(change))
        before = await conn.run_sync(schema_definition)
    with pytest.raises(RuntimeError, match=error):
        async with engine.begin() as conn:
            await conn.run_sync(postgres.upgrade, postgres.load_migrations(), schema)
    async with engine.begin() as conn:
        await conn.execute(text(f"SET LOCAL search_path TO {schema}, public"))
        assert await conn.run_sync(schema_definition) == before
        assert (
            await conn.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one() == "0023"
        if change.startswith("INSERT INTO thread ("):
            assert (
                await conn.execute(text("SELECT thread_id FROM thread"))
            ).scalar_one() == "keep-me"
        elif change.startswith("INSERT INTO thread_command"):
            assert (
                await conn.execute(text("SELECT command_id FROM thread_command_receipt"))
            ).scalar_one() == "keep-me"


async def test_external_dependency_rolls_back_all_table_replacements(
    migration_schema: tuple[AsyncEngine, str],
) -> None:
    engine, schema = migration_schema
    await install_legacy(engine, schema)
    async with engine.begin() as conn:
        await conn.execute(text(f"SET LOCAL search_path TO {schema}, public"))
        await conn.execute(text("CREATE VIEW keep_thread AS SELECT thread_id FROM thread"))
        before = await conn.run_sync(schema_definition)
    with pytest.raises(DBAPIError, match="depend"):
        async with engine.begin() as conn:
            await conn.run_sync(postgres.upgrade, postgres.load_migrations(), schema)
    async with engine.begin() as conn:
        await conn.execute(text(f"SET LOCAL search_path TO {schema}, public"))
        assert await conn.run_sync(schema_definition) == before
        assert (
            await conn.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one() == "0023"
