from unittest.mock import AsyncMock

import pytest

from agent import scheduler


@pytest.mark.parametrize(
    ("state", "handler_name", "expected"),
    [
        (
            scheduler.SchedulerState(
                task="agent_cost",
                thread_id="thread-1",
                run_id="invocation-1",
                attempt=0,
            ),
            "run_agent_cost_refresh",
            {
                "task": "agent_cost",
                "thread_id": "thread-1",
                "run_id": "invocation-1",
                "attempt": 0,
            },
        ),
        (
            scheduler.SchedulerState(
                task="session_cost",
                agent_thread_id="thread-1",
                run_id="langgraph-run-1",
                prepare_run_id="invocation-1",
                channel_id="C1",
                thread_ts="1.0",
                attempt=0,
            ),
            "run_session_cost_refresh",
            {
                "task": "session_cost",
                "agent_thread_id": "thread-1",
                "run_id": "langgraph-run-1",
                "prepare_run_id": "invocation-1",
                "channel_id": "C1",
                "thread_ts": "1.0",
                "attempt": 0,
            },
        ),
    ],
)
async def test_launch_preserves_legacy_cost_payloads(
    monkeypatch: pytest.MonkeyPatch,
    state: scheduler.SchedulerState,
    handler_name: str,
    expected: dict[str, object],
) -> None:
    handler = AsyncMock(return_value={"status": "updated"})
    monkeypatch.setattr(scheduler, handler_name, handler)

    result = await scheduler._launch(state, {})

    assert result == {"result": {"status": "updated"}}
    handler.assert_awaited_once_with(expected)
