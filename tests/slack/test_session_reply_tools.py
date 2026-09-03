import importlib
from typing import Any

import pytest

from agent import server

ask_tool = importlib.import_module("agent.slack.tools.ask_user_choice")
reply_tool = importlib.import_module("agent.slack.tools.reply_to_message")

CHANNEL = {
    "channel_id": "C-code",
    "thread_ts": "0",
    "surface": "slack_channel",
    "triggering_user_id": "U1",
}
THREAD = {
    "channel_id": "C1",
    "thread_ts": "1717171717.000100",
    "triggering_user_id": "U1",
}


def _patch(
    monkeypatch: pytest.MonkeyPatch, module: Any, location: dict[str, Any]
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def post(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"success": True, "message_ts": "2.0"}

    async def active(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return location

    monkeypatch.setattr(module, "post_session_message", post)
    monkeypatch.setattr(module, "get_active_slack_thread", active)
    monkeypatch.setattr(module, "get_langgraph_client", lambda: object())
    monkeypatch.setattr(
        module,
        "get_config",
        lambda: {"configurable": {"thread_id": "thread-1", "slack_thread": location}},
    )
    return captured


async def test_a_reply_lands_under_the_message_it_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted = _patch(monkeypatch, reply_tool, CHANNEL)

    result = await reply_tool.slack_reply_to_message("9.000", "Answering your question.")

    assert result["success"] is True
    assert posted["channel_id"] == "C-code"
    assert posted["thread_ts"] == "0"
    assert posted["post_thread_ts"] == "9.000"


@pytest.mark.parametrize(
    ("message_ts", "message", "expected"),
    [("", "text", "message_ts is required"), ("9.000", "  ", "Message cannot be empty")],
)
async def test_a_reply_needs_a_target_and_something_to_say(
    monkeypatch: pytest.MonkeyPatch, message_ts: str, message: str, expected: str
) -> None:
    posted = _patch(monkeypatch, reply_tool, CHANNEL)

    result = await reply_tool.slack_reply_to_message(message_ts, message)

    assert result["success"] is False
    assert expected in result["error"]
    assert posted == {}


async def test_replying_under_a_message_is_not_for_slack_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a thread surface a reply *is* how the agent speaks, so it has its own tool."""
    posted = _patch(monkeypatch, reply_tool, THREAD)

    result = await reply_tool.slack_reply_to_message("9.000", "Answering.")

    assert result["success"] is False
    assert "slack_thread_reply" in result["error"]
    assert posted == {}


async def test_a_choice_is_asked_at_session_level(monkeypatch: pytest.MonkeyPatch) -> None:
    posted = _patch(monkeypatch, ask_tool, CHANNEL)

    result = await ask_tool.ask_user_choice("Ship it?", ["Ship", "Hold"])

    assert result["success"] is True
    assert posted["post_thread_ts"] == "0"
    assert [button["text"]["text"] for button in posted["blocks"][1]["elements"]] == [
        "Ship",
        "Hold",
    ]


async def test_a_choice_can_be_asked_under_one_message(monkeypatch: pytest.MonkeyPatch) -> None:
    posted = _patch(monkeypatch, ask_tool, CHANNEL)

    await ask_tool.ask_user_choice("Ship it?", ["Ship", "Hold"], reply_to_ts="9.000")

    assert posted["post_thread_ts"] == "9.000"


async def test_a_choice_in_a_slack_thread_stays_in_that_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted = _patch(monkeypatch, ask_tool, THREAD)

    await ask_tool.ask_user_choice("Ship it?", ["Ship", "Hold"])

    assert posted["post_thread_ts"] == "1717171717.000100"


@pytest.mark.parametrize(
    ("question", "options", "expected"),
    [
        ("  ", ["A"], "question is required"),
        ("Pick", [], "options is required"),
        ("Pick", ["  "], "options is required"),
        ("Pick", ["a", "b", "c", "d", "e", "f"], "too many options"),
    ],
)
async def test_a_choice_needs_a_question_and_up_to_five_answers(
    monkeypatch: pytest.MonkeyPatch, question: str, options: list[str], expected: str
) -> None:
    posted = _patch(monkeypatch, ask_tool, CHANNEL)

    result = await ask_tool.ask_user_choice(question, options)

    assert result["success"] is False
    assert expected in result["error"]
    assert posted == {}


def test_a_channel_session_gets_the_reply_tool_that_matches_it() -> None:
    channel_tools = server._session_reply_tools({"slack_thread": CHANNEL})
    thread_tools = server._session_reply_tools({"slack_thread": THREAD})

    assert channel_tools == [server.slack_reply_to_message]
    assert thread_tools == [server.slack_thread_reply]
    assert server._session_reply_tools({}) == [server.slack_thread_reply]
