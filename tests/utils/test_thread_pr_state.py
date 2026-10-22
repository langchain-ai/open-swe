from unittest.mock import AsyncMock

import pytest

from openswe.utils import thread_pr_state
from openswe.utils.thread_pr_state import agent_thread_pr_state_lock


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []

    async def sleep(delay: float) -> None:
        recorded.append(delay)

    monkeypatch.setattr(thread_pr_state.asyncio, "sleep", sleep)
    return recorded


async def test_contended_lock_backs_off_up_to_a_cap(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    claim = AsyncMock(side_effect=[*(False for _ in range(8)), True])
    monkeypatch.setattr(thread_pr_state.event_claims, "claim", claim)
    monkeypatch.setattr(thread_pr_state.event_claims, "release", AsyncMock())

    async with agent_thread_pr_state_lock("thread-1"):
        pass

    assert len(sleeps) == 8
    for attempt, delay in enumerate(sleeps):
        ceiling = min(2.0, 0.1 * 2**attempt)
        assert ceiling / 2 <= delay <= ceiling


async def test_release_rides_out_brief_outages(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    release = AsyncMock(side_effect=[OSError("unavailable"), OSError("unavailable"), None])
    monkeypatch.setattr(thread_pr_state.event_claims, "claim", AsyncMock(return_value=True))
    monkeypatch.setattr(thread_pr_state.event_claims, "release", release)

    async with agent_thread_pr_state_lock("thread-1"):
        pass

    assert release.await_count == 3
