import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from agent.dashboard.profiles import get_my_profile
from agent.dashboard.repository_usage import get_repository_usage, record_repository_usage


@pytest.mark.asyncio
async def test_repository_usage_is_atomic_case_insensitive_and_user_scoped(
    registry_db: None,
) -> None:
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
            "agent.dashboard.repository_usage.postgres.transaction",
            side_effect=RuntimeError("offline"),
        ),
    ):
        await record_repository_usage("alice", "org/repo")
    assert "Failed to record repository usage" in caplog.text
