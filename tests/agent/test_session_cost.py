from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent import session_cost
from agent.utils import langsmith as ls_utils


class _LangSmithThreads:
    def __init__(self, stats: Any) -> None:
        self._stats = stats
        self.calls: list[dict[str, Any]] = []

    async def stats(self, thread_id: str, **kwargs: Any) -> Any:
        self.calls.append({"thread_id": thread_id, **kwargs})
        return self._stats


class _LangSmithClient:
    def __init__(self, roots: list[Any], stats: Any) -> None:
        self._roots = roots
        self.threads = _LangSmithThreads(stats)
        self.list_kwargs: dict[str, Any] = {}

    async def list_runs(self, **kwargs: Any):
        self.list_kwargs = kwargs
        for root in self._roots:
            yield root


async def test_langsmith_cost_requires_correlated_fresh_aggregate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_end = datetime(2026, 8, 18, 22, 0, tzinfo=UTC)
    client = _LangSmithClient(
        [SimpleNamespace(end_time=root_end)],
        SimpleNamespace(total_cost=1.234, last_end_time=root_end + timedelta(seconds=1)),
    )
    monkeypatch.setattr(ls_utils, "_build_prod_langsmith_client", lambda: client)
    monkeypatch.setattr(
        ls_utils, "_resolve_project_id_by_name", AsyncMock(return_value="project-id")
    )

    result = await ls_utils.get_langsmith_thread_cost("thread-1", "prepare-1")

    assert result is not None
    assert result.total_cost == 1.234
    assert client.list_kwargs["is_root"] is True
    assert "prepare_run_id" in client.list_kwargs["filter"]
    assert client.list_kwargs["select"] == ["end_time"]
    assert client.list_kwargs["limit"] == 20
    assert client.threads.calls == [
        {
            "thread_id": "thread-1",
            "session_id": "project-id",
            "selects": ["TOTAL_COST", "LAST_END_TIME"],
        }
    ]


async def test_langsmith_run_cost_filters_thread_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_end = datetime(2026, 8, 18, 22, 0, tzinfo=UTC)
    client = _LangSmithClient(
        [SimpleNamespace(end_time=root_end)],
        SimpleNamespace(total_cost=0.25, last_end_time=root_end),
    )
    monkeypatch.setattr(ls_utils, "_build_prod_langsmith_client", lambda: client)
    monkeypatch.setattr(
        ls_utils, "_resolve_project_id_by_name", AsyncMock(return_value="project-id")
    )

    result = await ls_utils.get_langsmith_thread_cost("thread-1", "prepare-1", run_only=True)

    assert result is not None
    assert result.total_cost == 0.25
    assert "prepare_run_id" in client.threads.calls[0]["filter"]


async def test_langsmith_cost_waits_for_thread_stats_freshness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_end = datetime(2026, 8, 18, 22, 0, tzinfo=UTC)
    client = _LangSmithClient(
        [SimpleNamespace(end_time=root_end)],
        SimpleNamespace(total_cost=2.0, last_end_time=root_end - timedelta(seconds=1)),
    )
    monkeypatch.setattr(ls_utils, "_build_prod_langsmith_client", lambda: client)
    monkeypatch.setattr(
        ls_utils, "_resolve_project_id_by_name", AsyncMock(return_value="project-id")
    )

    assert await ls_utils.get_langsmith_thread_cost("thread-1", "prepare-1") is None


async def test_refresh_updates_exact_mapped_slack_message_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Store:
        async def get_item(self, namespace: Any, key: str) -> dict[str, Any] | None:
            if key == "run:run-1":
                return {
                    "value": {
                        "run_id": "run-1",
                        "thread_ts": "1.0",
                        "message_ts": "1.1",
                    }
                }
            return None

    client: Any = SimpleNamespace(store=_Store())
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "Done"}},
        {"type": "actions", "elements": [{"type": "button", "action_id": "approve"}]},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "<https://app/agents/t1|Open in Web> • model-a • 10 main-agent tokens",
                }
            ],
        },
    ]
    monkeypatch.setattr(
        session_cost,
        "get_langsmith_thread_cost",
        AsyncMock(return_value=SimpleNamespace(total_cost=0.42)),
    )
    monkeypatch.setattr(
        session_cost,
        "fetch_slack_thread_message_by_ts",
        AsyncMock(
            return_value={
                "text": "Done <https://app/agents/t1|Open in Web> • model-a • 10 main-agent tokens",
                "blocks": blocks,
            }
        ),
    )
    update = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(session_cost, "update_slack_message", update)

    status, reason = await session_cost._refresh_once(_state(0), client)

    assert (status, reason) == ("updated", "Slack footer updated")
    update.assert_awaited_once()
    args = update.await_args
    assert args is not None
    assert args.args[:2] == ("C1", "1.1")
    assert args.args[2].endswith("model-a • $0.42")
    assert "main-agent tokens" not in args.args[2]
    assert args.kwargs["blocks"][1] == blocks[1]


class _Runs:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create(self, thread_id: str | None, assistant_id: str, **kwargs: Any) -> None:
        self.created.append({"thread_id": thread_id, "assistant_id": assistant_id, **kwargs})


class _Client:
    def __init__(self) -> None:
        self.runs = _Runs()


def _state(attempt: int) -> dict[str, Any]:
    return {
        "task": "session_cost",
        "agent_thread_id": "thread-1",
        "run_id": "run-1",
        "prepare_run_id": "prepare-1",
        "channel_id": "C1",
        "thread_ts": "1.0",
        "attempt": attempt,
    }


async def test_refresh_schedules_bounded_stateless_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    client: Any = _Client()
    monkeypatch.setattr(
        session_cost,
        "_refresh_once",
        AsyncMock(return_value=("pending", "LangSmith not ready")),
    )

    result = await session_cost.run_session_cost_refresh(_state(0), client=client)

    assert result == {
        "status": "retry_scheduled",
        "reason": "LangSmith not ready",
        "attempt": 1,
    }
    created = client.runs.created[0]
    assert created["thread_id"] is None
    assert created["assistant_id"] == "scheduler"
    assert created["after_seconds"] == 30
    assert created["on_completion"] == "delete"
    assert "webhook" not in created


async def test_refresh_stops_after_final_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    client: Any = _Client()
    monkeypatch.setattr(
        session_cost,
        "_refresh_once",
        AsyncMock(return_value=("pending", "LangSmith not ready")),
    )

    result = await session_cost.run_session_cost_refresh(_state(4), client=client)

    assert result == {"status": "exhausted", "reason": "LangSmith not ready"}
    assert client.runs.created == []
