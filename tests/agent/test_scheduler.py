from unittest.mock import AsyncMock

import pytest
from langsmith.sandbox import SandboxRetryableConnectionError

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


async def test_launch_runs_refresh_crons_registered_under_the_old_task_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tick = AsyncMock(return_value={"status": "refreshed"})
    monkeypatch.setattr(scheduler, "run_workspace_refresh_tick", tick)

    result = await scheduler._launch(
        scheduler.SchedulerState(task="environment_refresh", environment_slug="base"), {}
    )

    assert result == {"result": {"status": "refreshed"}}
    tick.assert_awaited_once_with("base", "full")


async def test_launch_returns_sandbox_unavailable_for_exhausted_transient_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = SandboxRetryableConnectionError("gateway unavailable")

    async def fail(*args: object, **kwargs: object) -> dict[str, object]:
        raise error

    monkeypatch.setattr(scheduler, "launch_scheduled_agent_run", fail)

    async def exhausted(*args: object, **kwargs: object) -> dict[str, object]:
        raise error

    monkeypatch.setattr(scheduler, "retry_transient_sandbox_errors", exhausted)

    result = await scheduler._launch(scheduler.SchedulerState(schedule_id="schedule-1"), {})

    assert result == {"result": {"status": "sandbox_unavailable"}}
