"""A Slack conversation of this deployment's channel moves through MDA, into any channel."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
import rt.slack_move as slack_move
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from managed_deepagents import SlackChannelTarget
from pydantic import BaseModel
from runtime_api.endpoint import Endpoint
from runtime_api.slack import (
    SLACK_MOVE,
    SLACK_MOVE_MARKER,
    SLACK_MOVED,
    SlackMoved,
    SlackMovePlan,
    SlackMoveRequest,
    SlackMoveResult,
)

_MOVED = {
    "success": True,
    "thread_id": "thread-1",
    "channel_id": "C1",
    "thread_ts": "200.1",
    "dashboard_url": "https://dashboard/thread-1",
}


class _Channel:
    provider = "slack"

    def __init__(self) -> None:
        self.posts: list[tuple[dict[str, object], dict[str, object]]] = []

    async def post(self, message: dict[str, object], **options: object) -> dict[str, str]:
        self.posts.append((message, options))
        return {"id": "200.1", "thread_id": "thread-1"}


class _Backend:
    def __init__(self, plan: SlackMovePlan) -> None:
        self.plan = plan
        self.calls: list[tuple[str, BaseModel]] = []

    async def call(self, _backend: object, endpoint: Endpoint, request: BaseModel) -> BaseModel:
        self.calls.append((endpoint.name, request))
        if endpoint is SLACK_MOVE:
            return self.plan
        assert endpoint is SLACK_MOVED
        return SlackMoveResult(result=_MOVED)


@pytest.fixture
def channel(monkeypatch: pytest.MonkeyPatch) -> _Channel:
    channel = _Channel()
    monkeypatch.setattr(slack_move, "_slack_channel", lambda _runtime: channel)
    monkeypatch.setattr(slack_move, "run_id", lambda: "run-1")
    return channel


def _backend(monkeypatch: pytest.MonkeyPatch, plan: SlackMovePlan) -> _Backend:
    backend = _Backend(plan)
    monkeypatch.setattr(slack_move, "call", backend.call)
    return backend


def _tool_call(**args: object) -> ToolCallRequest:
    return cast(
        ToolCallRequest,
        SimpleNamespace(
            tool_call={"name": "slack_move_thread", "args": args, "id": "call-1"},
            runtime=object(),
        ),
    )


async def _handled(_request: ToolCallRequest) -> ToolMessage:
    return ToolMessage(content="backend", tool_call_id="call-1")


def _middleware() -> slack_move.SlackMoveMiddleware:
    return slack_move.SlackMoveMiddleware(cast(Any, object()))


def test_a_move_in_its_own_channel_posts_through_mda_and_hands_the_thread_over(
    monkeypatch: pytest.MonkeyPatch, channel: _Channel
) -> None:
    backend = _backend(monkeypatch, SlackMovePlan(root_text="Moved here", via_mda=True))

    result = asyncio.run(_middleware().awrap_tool_call(_tool_call(message="Moved here"), _handled))

    assert isinstance(result, ToolMessage)
    assert json.loads(cast(str, result.content)) == _MOVED
    assert channel.posts == [
        (
            {"type": "content", "content": "Moved here"},
            {"new_thread": True, "hand_off": True, "to": None},
        )
    ]
    [(prepared, plan), (recorded, moved)] = backend.calls
    assert (prepared, recorded) == ("slack_move", "slack_moved")
    assert plan == SlackMoveRequest(run_id="run-1", message="Moved here", channel_id=None)
    # The backend records the root MDA posted, for the run its token names.
    assert moved == SlackMoved(run_id="run-1", thread_ts="200.1", mark_source=False)


def test_a_move_into_another_channel_posts_there_through_mda(
    monkeypatch: pytest.MonkeyPatch, channel: _Channel
) -> None:
    plan = SlackMovePlan(root_text="Moved here", via_mda=True, channel_id="C2")
    backend = _backend(monkeypatch, plan)

    result = asyncio.run(
        _middleware().awrap_tool_call(_tool_call(message="Moved", channel_id="C2"), _handled)
    )

    assert isinstance(result, ToolMessage)
    assert json.loads(cast(str, result.content)) == _MOVED
    [(_message, options)] = channel.posts
    assert options == {
        "new_thread": True,
        "hand_off": True,
        "to": SlackChannelTarget(conversation_id="C2"),
    }
    [(_prepared, request), (_recorded, moved)] = backend.calls
    assert cast(SlackMoveRequest, request).channel_id == "C2"
    assert moved == SlackMoved(run_id="run-1", thread_ts="200.1", channel_id="C2")


def test_a_thread_the_backend_holds_moves_with_the_backends_tool(
    monkeypatch: pytest.MonkeyPatch, channel: _Channel
) -> None:
    _backend(monkeypatch, SlackMovePlan(root_text="Moved here", via_mda=False))

    result = asyncio.run(
        _middleware().awrap_tool_call(_tool_call(message="Moved", channel_id="C2"), _handled)
    )

    assert isinstance(result, ToolMessage)
    assert result.content == "backend"
    assert channel.posts == []


def test_a_move_the_checks_stop_posts_nothing(
    monkeypatch: pytest.MonkeyPatch, channel: _Channel
) -> None:
    refused = {"success": False, "error": "Private threads cannot be moved"}
    _backend(monkeypatch, SlackMovePlan(result=refused))

    result = asyncio.run(_middleware().awrap_tool_call(_tool_call(message="Moved"), _handled))

    assert isinstance(result, ToolMessage)
    assert json.loads(cast(str, result.content)) == refused
    assert channel.posts == []


def test_a_thread_no_slack_conversation_holds_moves_on_the_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_move, "_slack_channel", lambda _runtime: None)

    result = asyncio.run(_middleware().awrap_tool_call(_tool_call(message="Moved"), _handled))

    assert isinstance(result, ToolMessage)
    assert result.content == "backend"


def test_a_bare_breakout_moves_the_thread_before_any_model_call(
    monkeypatch: pytest.MonkeyPatch, channel: _Channel
) -> None:
    backend = _backend(monkeypatch, SlackMovePlan(root_text="`/breakout`: Fix it", via_mda=True))
    marked = HumanMessage(content="/breakout", additional_kwargs={SLACK_MOVE_MARKER: True})

    update = asyncio.run(_middleware().abefore_model({"messages": [marked]}, cast(Any, object())))

    assert update is not None
    assert update["jump_to"] == "end"
    [answer] = cast(list[AIMessage], update["messages"])
    assert "Moved" in cast(str, answer.content)
    [(_prepared, plan), (_recorded, moved)] = backend.calls
    # No message: the backend writes the root, as its own move does.
    assert cast(SlackMoveRequest, plan).message is None
    assert cast(SlackMoved, moved).mark_source is True
    assert len(channel.posts) == 1


def test_a_bare_breakout_into_another_channel_moves_the_thread_there(
    monkeypatch: pytest.MonkeyPatch, channel: _Channel
) -> None:
    plan = SlackMovePlan(root_text="`/breakout`: Fix it", via_mda=True, channel_id="C2")
    backend = _backend(monkeypatch, plan)
    marked = HumanMessage(content="/breakout #eng", additional_kwargs={SLACK_MOVE_MARKER: "C2"})

    update = asyncio.run(_middleware().abefore_model({"messages": [marked]}, cast(Any, object())))

    assert update is not None
    assert update["jump_to"] == "end"
    [(_prepared, request), (_recorded, moved)] = backend.calls
    assert cast(SlackMoveRequest, request).channel_id == "C2"
    assert cast(SlackMoved, moved).channel_id == "C2"
    [(_message, options)] = channel.posts
    assert options["to"] == SlackChannelTarget(conversation_id="C2")


def test_any_other_turn_calls_the_model(monkeypatch: pytest.MonkeyPatch, channel: _Channel) -> None:
    backend = _backend(monkeypatch, SlackMovePlan(root_text="x", via_mda=True))

    update = asyncio.run(
        _middleware().abefore_model(
            {"messages": [HumanMessage(content="/breakout")]}, cast(Any, object())
        )
    )

    assert update is None
    assert backend.calls == []
    assert channel.posts == []
