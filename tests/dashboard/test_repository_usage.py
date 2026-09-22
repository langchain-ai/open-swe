import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import text

from agent.dashboard.profiles import get_my_profile
from agent.dashboard.repository_usage import get_repository_usage, record_repository_usage
from agent.database import postgres
from agent.users.models import User


@pytest.mark.asyncio
async def test_repository_usage_is_atomic_case_insensitive_and_user_scoped(
    registry_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice,bob,renamed")
    alice_user = await User.sign_in("github", "101", login="alice")
    await User.sign_in("github", "102", login="bob")
    await asyncio.gather(
        *(record_repository_usage("Alice", "Langchain-AI/Open-SWE") for _ in range(8))
    )
    await record_repository_usage("alice", "langchain-ai/langchain")
    await record_repository_usage("bob", "langchain-ai/open-swe")

    alice = await get_repository_usage("ALICE")
    assert [(row["repo"], row["use_count"]) for row in alice] == [
        ("langchain-ai/langchain", 1),
        ("langchain-ai/open-swe", 8),
    ]
    assert datetime.fromisoformat(alice[0]["last_used_at"]).tzinfo is not None
    assert (await get_repository_usage("bob"))[0]["use_count"] == 1
    assert await get_repository_usage("charlie") == []
    renamed = await User.sign_in("github", "101", login="renamed")
    assert renamed.id == alice_user.id
    assert await get_repository_usage("renamed") == alice
    await record_repository_usage("renamed", "langchain-ai/open-swe")
    assert (await get_repository_usage("renamed"))[0]["use_count"] == 9
    await User.sign_in("github", "103", login="alice")
    assert await get_repository_usage("alice") == []


@pytest.mark.asyncio
async def test_usage_is_exposed_without_saved_profile() -> None:
    usage = [{"repo": "org/repo", "use_count": 2, "last_used_at": "2026-09-01T12:00:00+00:00"}]
    with (
        patch("agent.dashboard.profiles.get_profile", AsyncMock(return_value=None)),
        patch("agent.dashboard.profiles.get_repository_usage", AsyncMock(return_value=usage)),
    ):
        assert await get_my_profile({"sub": "alice"}) == {"repository_usage": usage}


@pytest.mark.asyncio
async def test_usage_write_failure_does_not_fail_thread_creation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        patch("agent.dashboard.repository_usage.postgres.configured", return_value=True),
        patch(
            "agent.dashboard.repository_usage.User.for_login",
            side_effect=RuntimeError("offline"),
        ),
    ):
        await record_repository_usage("alice", "org/repo")
    assert "Failed to record repository usage" in caplog.text


@pytest.mark.asyncio
async def test_migration_preserves_and_merges_history(registry_db: None) -> None:
    schema = f"usage_migration_{uuid4().hex}"
    user_id = uuid4()
    async with postgres.transaction() as conn:
        await conn.execute(text(f"CREATE SCHEMA {schema}"))
        try:
            migrations = postgres.load_migrations()
            await conn.run_sync(postgres.upgrade, migrations, schema, "0026")
            await conn.execute(text("INSERT INTO users (id) VALUES (:id)"), {"id": user_id})
            await conn.execute(
                text("""
                    INSERT INTO user_identity (provider, external_id, user_id, login)
                    VALUES ('github', '101', :id, 'alice'), ('github', '102', :id, 'old-alice')
                """),
                {"id": user_id},
            )
            await conn.execute(
                text("""
                INSERT INTO user_repository_usage (login, repo, use_count, last_used_at)
                VALUES ('alice', 'org/repo', 3, '2026-09-01T00:00:00Z'),
                    ('old-alice', 'org/repo', 5, '2026-09-02T00:00:00Z')
            """)
            )
            await conn.run_sync(postgres.upgrade, migrations, schema)
            row = (await conn.execute(text("SELECT * FROM user_repository_usage"))).mappings().one()
            assert row["user_id"] == user_id
            assert row["use_count"] == 8
            assert row["last_used_at"].isoformat() == "2026-09-02T00:00:00+00:00"
            assert "login" not in row
            await conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
            assert (
                await conn.execute(text("SELECT count(*) FROM user_repository_usage"))
            ).scalar_one() == 0
        finally:
            await conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
