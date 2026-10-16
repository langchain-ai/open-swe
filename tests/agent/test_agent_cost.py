import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openswe import agent_cost


class _Runs:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create(self, thread_id: str | None, assistant_id: str, **kwargs: Any) -> None:
        self.created.append({"thread_id": thread_id, "assistant_id": assistant_id, **kwargs})


class _Client:
    def __init__(self) -> None:
        self.runs = _Runs()


def _state(attempt: int) -> dict[str, Any]:
    return {"task": "agent_cost", "thread_id": "thread-1", "run_id": "run-1", "attempt": attempt}


@pytest.mark.asyncio
async def test_refresh_gives_up_when_cost_never_appears(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(agent_cost, "get_langsmith_thread_cost", AsyncMock(return_value=None))
    client = _Client()

    with caplog.at_level(logging.INFO, logger=agent_cost.__name__):
        result = await agent_cost.run_agent_cost_refresh(_state(4), client=client)

    assert result == {"status": "exhausted", "reason": "LangSmith cost unavailable"}
    assert client.runs.created == []
    assert "Agent cost refresh exhausted" in caplog.text
