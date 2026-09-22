from unittest.mock import AsyncMock

import httpx
import pytest
from langgraph_sdk.errors import ConflictError, InternalServerError

from agent.utils import thread_pr_state
from agent.utils.thread_pr_state import agent_thread_pr_state_lock


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", "http://test/threads"))


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []

    async def sleep(delay: float) -> None:
        recorded.append(delay)

    monkeypatch.setattr(thread_pr_state.asyncio, "sleep", sleep)
    return recorded


async def test_contended_lock_backs_off_up_to_a_cap(sleeps: list[float]) -> None:
    client = AsyncMock()
    client.threads.create.side_effect = [
        *(ConflictError("held", response=_response(409), body=None) for _ in range(8)),
        None,
    ]

    async with agent_thread_pr_state_lock(client, "thread-1"):
        pass

    for attempt, delay in enumerate(sleeps):
        ceiling = min(2.0, 0.1 * 2**attempt)
        assert ceiling / 2 <= delay <= ceiling


async def test_release_retries_transient_failures(sleeps: list[float]) -> None:
    client = AsyncMock()
    unavailable = InternalServerError("unavailable", response=_response(503), body=None)
    client.threads.delete.side_effect = [unavailable, unavailable, None]

    async with agent_thread_pr_state_lock(client, "thread-1"):
        pass

    assert client.threads.delete.await_count == 3
