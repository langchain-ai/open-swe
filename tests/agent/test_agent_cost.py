from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent import agent_cost
from agent.utils.langsmith import LangSmithCostUnavailable, LangSmithThreadCost


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
async def test_refresh_writes_cumulative_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    monkeypatch.setattr(
        agent_cost,
        "get_langsmith_thread_cost",
        AsyncMock(return_value=LangSmithThreadCost(1.25, now, now)),
    )
    record = AsyncMock()
    monkeypatch.setattr(agent_cost, "record_agent_run_cost", record)

    result = await agent_cost.run_agent_cost_refresh(_state(0), client=_Client())

    assert result == {"status": "updated"}
    record.assert_awaited_once_with(run_id="run-1", cost_usd=1.25)


@pytest.mark.asyncio
async def test_refresh_noops_when_cost_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_cost,
        "get_langsmith_thread_cost",
        AsyncMock(side_effect=LangSmithCostUnavailable("not configured")),
    )
    record = AsyncMock()
    monkeypatch.setattr(agent_cost, "record_agent_run_cost", record)

    result = await agent_cost.run_agent_cost_refresh(_state(0), client=_Client())

    assert result == {"status": "unavailable", "reason": "not configured"}
    record.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_gives_up_when_cost_never_appears(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_cost, "get_langsmith_thread_cost", AsyncMock(return_value=None))
    client = _Client()

    result = await agent_cost.run_agent_cost_refresh(_state(4), client=client)

    assert result == {"status": "exhausted", "reason": "LangSmith cost unavailable"}
    assert client.runs.created == []
