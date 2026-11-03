"""The backend checks and records a move MDA's Slack channel makes, as the run on its thread."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from runtime_api.slack import SlackMoved, SlackMoveRequest

from openswe.slack import channel_moves
from openswe.slack.tools.move_thread import CheckedMove

_ACTIVE = {"channel_id": "C1", "thread_ts": "1.0"}
_CONFIG = {
    "configurable": {
        "thread_id": "thread-1",
        "slack_thread": {
            "channel_id": "C1",
            "thread_ts": "1.0",
            "triggering_event_ts": "1.5",
            "triggering_user_id": "U1",
        },
    }
}


@pytest.fixture(autouse=True)
def _run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(channel_moves, "get_config", lambda: _CONFIG)
    monkeypatch.setattr(
        channel_moves, "dashboard_thread_url", lambda thread_id: f"https://dash/{thread_id}"
    )


def _checked(monkeypatch: pytest.MonkeyPatch, target: str = "C1") -> AsyncMock:
    check = AsyncMock(
        return_value=CheckedMove(cast(Any, object()), "thread-1", _ACTIVE, target, "Breakout")
    )
    monkeypatch.setattr(channel_moves, "check_move", check)
    return check


def _held(monkeypatch: pytest.MonkeyPatch, channel: str | None = "slack") -> None:
    indexed = SimpleNamespace(metadata={"channel": channel})
    monkeypatch.setattr(channel_moves.index, "get", AsyncMock(return_value=indexed))


@pytest.mark.parametrize(
    ("target", "channel", "via_mda"),
    [("C1", "slack", True), ("C2", "slack", False), ("C1", None, False)],
    ids=["own channel", "another channel", "a thread the backend holds"],
)
async def test_only_a_move_in_its_own_channel_of_an_mda_conversation_goes_through_mda(
    monkeypatch: pytest.MonkeyPatch, target: str, channel: str | None, via_mda: bool
) -> None:
    check = _checked(monkeypatch, target)
    _held(monkeypatch, channel)

    plan = await channel_moves.prepare_channel_move(
        SlackMoveRequest(run_id="run-1", message="Breakout", channel_id=target)
    )

    check.assert_awaited_once_with("Breakout", target)
    assert plan.via_mda is via_mda
    assert plan.result is None
    assert plan.root_text is not None
    assert plan.root_text.startswith("Breakout")
    assert "https://dash/thread-1" in plan.root_text


async def test_a_move_the_checks_stop_answers_with_their_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refused = {"success": False, "error": "Private threads cannot be moved"}
    monkeypatch.setattr(channel_moves, "check_move", AsyncMock(return_value=refused))

    plan = await channel_moves.prepare_channel_move(SlackMoveRequest(message="Breakout"))

    assert plan.result == refused
    assert (plan.root_text, plan.via_mda) == (None, False)


async def test_a_bare_breakout_names_the_thread_its_command_and_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    check = _checked(monkeypatch)
    _held(monkeypatch)
    client = SimpleNamespace(
        threads=SimpleNamespace(get=AsyncMock(return_value={"metadata": {"title": "Fix it"}}))
    )
    monkeypatch.setattr(channel_moves, "langgraph_client", lambda: client)
    source = AsyncMock(return_value="<https://slack/p15|(source)>")
    monkeypatch.setattr(channel_moves, "source_thread_line", source)

    await channel_moves.prepare_channel_move(SlackMoveRequest(run_id="run-1"))

    source.assert_awaited_once_with("C1", "1.5")
    check.assert_awaited_once_with(
        "`/breakout`: Fix it · <https://slack/p15|(source)> · <@U1>", None
    )


@pytest.mark.parametrize("mark_source", [False, True])
async def test_recording_a_move_rebinds_the_runs_own_thread_to_the_new_root(
    monkeypatch: pytest.MonkeyPatch, mark_source: bool
) -> None:
    client = object()
    monkeypatch.setattr(channel_moves, "langgraph_client", lambda: client)
    monkeypatch.setattr(channel_moves, "get_active_slack_thread", AsyncMock(return_value=_ACTIVE))
    moved = {"success": True, "thread_id": "thread-1", "channel_id": "C1", "thread_ts": "200.1"}
    record = AsyncMock(return_value=moved)
    monkeypatch.setattr(channel_moves, "record_slack_move", record)
    marked = AsyncMock()
    monkeypatch.setattr(channel_moves, "mark_broken_out", marked)
    forget = Mock()
    runtimes = SimpleNamespace(mda_runtime=SimpleNamespace(forget_channel_conversation=forget))
    monkeypatch.setattr(channel_moves, "RUNTIMES", runtimes)

    result = await channel_moves.record_channel_move(
        SlackMoved(run_id="run-1", thread_ts="200.1", mark_source=mark_source)
    )

    assert result.result == moved
    # The thread is the run's own, from its config; MDA's owner of it is read again.
    record.assert_awaited_once_with(client, "thread-1", _ACTIVE, "C1", "200.1")
    forget.assert_called_once_with("thread-1")
    if mark_source:
        marked.assert_awaited_once_with("C1", "1.0", "C1", "200.1")
    else:
        marked.assert_not_awaited()


def test_a_recorded_move_names_a_slack_message() -> None:
    with pytest.raises(ValueError, match="pattern"):
        SlackMoved(thread_ts="C1/../2")
