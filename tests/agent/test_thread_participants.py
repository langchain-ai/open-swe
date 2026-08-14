from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.utils import thread_participants, ttl_cache
from agent.utils.thread_participants import resolve_participant
from agent.utils.thread_participants import thread_participants as roster


@pytest.fixture(autouse=True)
def _clear_cache() -> Any:
    ttl_cache.clear()
    yield
    ttl_cache.clear()


def _client(metadata: dict[str, Any]) -> MagicMock:
    client = MagicMock()
    client.threads.get = AsyncMock(return_value={"metadata": metadata})
    return client


class TestRoster:
    async def test_owner_first_then_joiners(self) -> None:
        client = _client({"github_login": "ramon", "participant_logins": ["paarth", "mason"]})

        assert await roster(client, "t1") == ["ramon", "paarth", "mason"]

    async def test_deduplicates_owner_listed_as_participant(self) -> None:
        client = _client({"github_login": "ramon", "participant_logins": ["ramon", "paarth"]})

        assert await roster(client, "t1") == ["ramon", "paarth"]


class TestResolveParticipant:
    async def test_rejects_login_outside_the_thread(self) -> None:
        with (
            patch.object(thread_participants, "current_thread_id", return_value="t1"),
            patch.object(
                thread_participants, "thread_participants", AsyncMock(return_value=["ramon"])
            ),
        ):
            with pytest.raises(ValueError, match="not a participant"):
                await resolve_participant("stranger")

    async def test_accepts_a_participant(self) -> None:
        with (
            patch.object(thread_participants, "current_thread_id", return_value="t1"),
            patch.object(
                thread_participants,
                "thread_participants",
                AsyncMock(return_value=["ramon", "paarth"]),
            ),
        ):
            assert await resolve_participant(" paarth ") == "paarth"

    async def test_requires_a_value(self) -> None:
        with pytest.raises(ValueError, match="on_behalf_of is required"):
            await resolve_participant("")
