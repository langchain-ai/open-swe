"""Mirroring a run's tool progress into a Linear agent session."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent.linear import activities
from agent.linear.client import LinearError
from agent.linear.schema import ActionContent


def _event(method: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "event",
        "event_id": "1-0",
        "method": method,
        "params": {"namespace": [], "timestamp": 1, "data": data},
    }


def _lifecycle(phase: str) -> dict[str, Any]:
    event = _event("lifecycle", {"event": phase})
    event["event_id"] = f"synth:run-1:lc||{phase}"
    return event


def _started(call_id: str, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return _event(
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
    events = [
        _started("old-call", "read_file", {"file_path": "/workspace/agent/before.py"}),
        _lifecycle("running"),
        _started("call-1", "read_file", {"file_path": "/workspace/agent/server.py"}),
        _lifecycle("completed"),
    ]

    class ThreadStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        def subscribe(self, channels):
            assert channels == ["lifecycle", "tools"]

            async def iterator():
                for event in events:
                    yield event

            return iterator()

    client = AsyncMock()
    client.threads.stream = lambda *_args, **_kwargs: ThreadStream()

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
