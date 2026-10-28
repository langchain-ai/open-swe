"""Migrations that give each GitHub login to one account and key thread owners by user id."""

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

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
        async with engine.begin() as conn:
            await conn.run_sync(
                postgres.upgrade, postgres.load_migrations(), schema, "4fd72cf43b71"
            )
        yield engine, schema
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await engine.dispose()


async def _person(conn: AsyncConnection, external_id: str, login: str, seen: datetime) -> str:
    user_id = str(uuid4())
    await conn.execute(text("INSERT INTO users (id) VALUES (:id)"), {"id": user_id})
    await conn.execute(
        text("""
            INSERT INTO user_identity (user_id, provider, external_id, login, last_seen_at)
            VALUES (:user_id, 'github', :external_id, :login, :seen)
        """),
        {"user_id": user_id, "external_id": external_id, "login": login, "seen": seen},
    )
    return user_id


async def test_a_reassigned_login_and_the_threads_it_names_go_to_the_account_seen_last(
    migration_schema: tuple[AsyncEngine, str],
) -> None:
    engine, schema = migration_schema
    threads = {
        "owned": {"owner_login": "ADA"},
        "review": {"source": "review_chat", "github_login": "ada"},
        "stranger": {"owner_login": "nobody"},
        "keyed": {"owner_login": "ada", "owner_user_id": "already-set"},
    }
    async with engine.begin() as conn:
        await conn.execute(text(f"SET LOCAL search_path TO {schema}, public"))
        renamed = await _person(conn, "1", "ada", datetime(2026, 1, 1, tzinfo=UTC))
        holder = await _person(conn, "2", "Ada", datetime(2026, 2, 1, tzinfo=UTC))
        for thread_id, metadata in threads.items():
            for insert in (
                "INSERT INTO thread_index (id, thread_id, runtime, metadata)"
                " VALUES (gen_random_uuid(), :thread_id, 'mda', CAST(:metadata AS jsonb))",
                "INSERT INTO thread (thread_id, metadata)"
                " VALUES (:thread_id, CAST(:metadata AS jsonb))",
            ):
                await conn.execute(
                    text(insert), {"thread_id": thread_id, "metadata": json.dumps(metadata)}
                )

    async with engine.begin() as conn:
        await conn.run_sync(postgres.upgrade, postgres.load_migrations(), schema, "da2fc6ed239c")

    async with engine.begin() as conn:
        await conn.execute(text(f"SET LOCAL search_path TO {schema}, public"))
        logins = dict(
            (await conn.execute(text("SELECT CAST(user_id AS text), login FROM user_identity")))
            .tuples()
            .all()
        )
        assert logins == {renamed: "", holder: "Ada"}
        for table in ("thread_index", "thread"):
            owners = dict(
                (
                    await conn.execute(
                        text(f"SELECT thread_id, metadata->>'owner_user_id' FROM {table}")
                    )
                )
                .tuples()
                .all()
            )
            assert owners == {
                "owned": holder,
                "review": holder,
                "stranger": None,
                "keyed": "already-set",
            }
