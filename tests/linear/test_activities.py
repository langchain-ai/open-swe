"""Mirroring a run's tool progress into a Linear agent session."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent.linear import activities
from agent.linear.client import LinearError
from agent.linear.schema import ActionContent


class _Part:
    def __init__(self, event: str, data: dict[str, Any]) -> None:
        self.event = event
        self.data = data


def _started(call_id: str, tool_name: str, tool_input: dict[str, Any]) -> _Part:
    return _Part(
        "tools",
        {
            "event": "tool-started",
            "tool_call_id": call_id,
            "tool_name": tool_name,
            "input": tool_input,
        },
    )


@pytest.fixture
def linear(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    client = AsyncMock()
    monkeypatch.setattr(activities, "linear_client", lambda: client)
    return client


async def test_tool_starts_become_ephemeral_actions(
    linear: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(activities, "COMPLETION_WEBHOOK_URL", "https://open-swe/complete")
    parts = [_started("call-1", "read_file", {"file_path": "/workspace/agent/server.py"})]

    async def join_stream(*_args: object, **_kwargs: object):
        for part in parts:
            yield part

    client = AsyncMock()
    client.runs.join_stream = join_stream
    client.runs.get.return_value = {"status": "success"}

    await activities.stream_linear_activities(
        client=client, thread_id="thread-1", run_id="run-1", session_id="session-1"
    )

    linear.create_agent_activity.assert_awaited_once()
    call = linear.create_agent_activity.await_args
    assert call is not None
    content = call.args[1]
    assert isinstance(content, ActionContent)
    assert content.action == "Reading server.py"
    assert call.kwargs["ephemeral"] is True


async def test_rapid_steps_coalesce_to_the_latest(
    linear: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = 100.0
    monkeypatch.setattr(activities, "monotonic", lambda: clock)
    stream = activities.LinearActivityStream(session_id="session-1", run_id="run-1")

    stream.consume(_started("call-1", "read_file", {"file_path": "a.py"}))
    stream.consume(_started("call-2", "read_file", {"file_path": "b.py"}))
    await stream.flush(force=True)

    stream.consume(_started("call-3", "read_file", {"file_path": "c.py"}))
    clock = 101.0
    await stream.flush()

    assert linear.create_agent_activity.await_count == 1
    assert linear.create_agent_activity.await_args.args[1].action == "Reading b.py"

    clock = 103.0
    await stream.flush()
    assert linear.create_agent_activity.await_count == 2
    assert linear.create_agent_activity.await_args.args[1].action == "Reading c.py"


async def test_a_linear_failure_disables_emission_without_raising(linear: AsyncMock) -> None:
    linear.create_agent_activity.side_effect = LinearError([{"message": "nope"}])
    stream = activities.LinearActivityStream(session_id="session-1", run_id="run-1")

    stream.consume(_started("call-1", "read_file", {"file_path": "a.py"}))
    await stream.flush(force=True)
    stream.consume(_started("call-2", "read_file", {"file_path": "b.py"}))
    await stream.flush(force=True)

    assert stream.disabled
    assert linear.create_agent_activity.await_count == 1


async def test_the_completion_webhook_owns_the_terminal_activity(
    linear: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(activities, "COMPLETION_WEBHOOK_URL", "https://open-swe/complete")
    stream = activities.LinearActivityStream(session_id="session-1", run_id="run-1")

    await stream.finish("error")

    linear.create_agent_activity.assert_not_called()


async def test_a_failure_is_reported_when_no_completion_webhook_is_wired(
    linear: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(activities, "COMPLETION_WEBHOOK_URL", None)
    stream = activities.LinearActivityStream(session_id="session-1", run_id="run-1")

    await stream.finish("error")

    linear.create_agent_activity.assert_awaited_once()
    assert linear.create_agent_activity.await_args.args[1].type == "error"


async def test_an_interrupted_run_is_not_reported_as_a_failure(
    linear: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(activities, "COMPLETION_WEBHOOK_URL", None)
    stream = activities.LinearActivityStream(session_id="session-1", run_id="run-1")

    await stream.finish("interrupted")

    linear.create_agent_activity.assert_not_called()
