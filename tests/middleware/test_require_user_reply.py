from collections.abc import Awaitable, Callable
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import wrap_model_call
from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent.middleware.require_user_reply import (
    SLACK_REPLY_SURFACE,
    WEB_REPLY_SURFACE,
    RequireUserReplyMiddleware,
    _turn_tail,
)

TOOL = "slack_reply"
NO_REPLY_TOOL = "slack_no_reply_needed"


def _middleware(**kwargs: Any) -> RequireUserReplyMiddleware:
    return RequireUserReplyMiddleware(
        TOOL, NO_REPLY_TOOL, initial_surface=SLACK_REPLY_SURFACE, **kwargs
    )


def _state(*messages: Any, **values: Any) -> AgentState:
    return cast(
        AgentState, {"messages": list(messages), "reply_surface": SLACK_REPLY_SURFACE, **values}
    )


def _call(name: str, call_id: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def _reply(call_id: str, response_type: str = "final") -> AIMessage:
    return _call(TOOL, call_id, message="done", response_type=response_type)


def _result(call_id: str, *, success: bool = True) -> ToolMessage:
    return ToolMessage(content=f'{{"success": {str(success).lower()}}}', tool_call_id=call_id)


def _runtime() -> Any:
    return MagicMock()


class TestRequireUserReplyMiddleware:
    @pytest.mark.asyncio
    async def test_reinvokes_when_the_turn_ends_with_undelivered_text(self) -> None:
        result = await _middleware().aafter_model(
            _state(HumanMessage(content="what is up"), AIMessage(content="all good")),
            _runtime(),
        )

        assert result == {"reply_nudges": 1, "reply_nudge_pending": True, "jump_to": "model"}

    @pytest.mark.asyncio
    async def test_lets_the_turn_end_once_the_reply_tool_ran(self) -> None:
        result = await _middleware().aafter_model(
            _state(
                HumanMessage(content="what is up"),
                _reply("call-1"),
                _result("call-1"),
                AIMessage(content="done"),
            ),
            _runtime(),
        )

        assert result == {"reply_nudge_pending": False, "reply_nudges": 0}

    @pytest.mark.asyncio
    async def test_the_opening_acknowledgement_does_not_end_the_turn(self) -> None:
        """The Slack prompt orders an ack before any work; it is not the answer."""
        result = await _middleware().aafter_model(
            _state(
                HumanMessage(content="what is up"),
                _reply("call-1", "progress"),
                _result("call-1"),
                AIMessage(content="all good"),
            ),
            _runtime(),
        )

        assert result == {"reply_nudges": 1, "reply_nudge_pending": True, "jump_to": "model"}

    @pytest.mark.asyncio
    async def test_declining_to_reply_also_ends_the_turn(self) -> None:
        result = await _middleware().aafter_model(
            _state(
                HumanMessage(content="thanks all"),
                _call(NO_REPLY_TOOL, "call-1"),
                _result("call-1"),
                AIMessage(content=""),
            ),
            _runtime(),
        )

        assert result == {"reply_nudge_pending": False, "reply_nudges": 0}

    @pytest.mark.asyncio
    async def test_a_reply_slack_rejected_does_not_count(self) -> None:
        result = await _middleware().aafter_model(
            _state(
                HumanMessage(content="what is up"),
                _reply("call-1"),
                _result("call-1", success=False),
                AIMessage(content="I could not post that"),
            ),
            _runtime(),
        )

        assert result == {"reply_nudges": 1, "reply_nudge_pending": True, "jump_to": "model"}

    @pytest.mark.asyncio
    async def test_leaves_a_web_turn_alone(self) -> None:
        middleware = RequireUserReplyMiddleware(
            TOOL, NO_REPLY_TOOL, initial_surface=WEB_REPLY_SURFACE
        )

        result = await middleware.aafter_model(
            _state(
                HumanMessage(content="what is up"),
                AIMessage(content="all good"),
                reply_surface=WEB_REPLY_SURFACE,
            ),
            _runtime(),
        )

        assert result == {"reply_nudge_pending": False}

    @pytest.mark.asyncio
    async def test_leaves_a_turn_that_is_still_calling_tools_alone(self) -> None:
        result = await _middleware().aafter_model(
            _state(HumanMessage(content="what is up"), _call("execute", "call-2")),
            _runtime(),
        )

        assert result == {"reply_nudge_pending": False}

    @pytest.mark.asyncio
    async def test_posts_the_final_message_once_the_budget_is_spent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import agent.slack.tools.reply as reply_tool

        posted = AsyncMock(return_value={"success": True})
        monkeypatch.setattr(reply_tool, "slack_reply", posted)
        state = _state(
            HumanMessage(content="what is up"),
            AIMessage(content="all good"),
            reply_nudges=2,
        )

        result = await _middleware(max_retries=2).aafter_model(state, _runtime())

        assert result == {"reply_nudge_pending": False, "reply_nudges": 0}
        assert posted.await_args.args[0] == "all good"

    @pytest.mark.asyncio
    async def test_a_spent_budget_with_nothing_to_say_posts_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import agent.slack.tools.reply as reply_tool

        posted = AsyncMock(return_value={"success": True})
        monkeypatch.setattr(reply_tool, "slack_reply", posted)

        await _middleware(max_retries=2).aafter_model(
            _state(HumanMessage(content="what is up"), AIMessage(content=""), reply_nudges=2),
            _runtime(),
        )

        posted.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("pending", [False, True])
    @pytest.mark.parametrize(
        "system",
        [
            None,
            SystemMessage(content="base prompt"),
            SystemMessage(content=[{"type": "text", "text": "base prompt"}]),
        ],
    )
    async def test_nudge_preserves_request_prefix_and_state(
        self, pending: bool, system: SystemMessage | None
    ) -> None:
        state = _state(
            HumanMessage(content="what is up"),
            _reply("call-1", "progress"),
            _result("call-1"),
            AIMessage(content="all good"),
            reply_nudge_pending=pending,
        )
        original = state["messages"].copy()
        request = ModelRequest(
            model=FakeListChatModel(responses=["unused"]),
            messages=state["messages"],
            system_message=system,
            tools=[{"name": TOOL, "parameters": {"type": "object"}}],
            state=state,
        )
        response = ModelResponse(result=[AIMessage(content="done")])
        handler = AsyncMock(return_value=response)

        assert await _middleware().awrap_model_call(request, handler) is response

        outgoing = handler.await_args.args[0]
        assert outgoing.system_message is system
        assert outgoing.tools is request.tools
        assert outgoing.messages[: len(original)] == original
        assert request.messages == original
        assert state["messages"] == original
        assert _turn_tail(state["messages"]) == original[1:]
        if pending:
            assert len(outgoing.messages) == len(original) + 1
            reminder = outgoing.messages[-1]
            assert isinstance(reminder, HumanMessage)
            assert TOOL in reminder.content
            assert NO_REPLY_TOOL in reminder.content
        else:
            assert outgoing is request

    @pytest.mark.asyncio
    async def test_retries_do_not_persist_reminders_or_split_the_user_turn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import agent.slack.tools.reply as reply_tool

        posted = AsyncMock(return_value={"success": True})
        monkeypatch.setattr(reply_tool, "slack_reply", posted)
        seen: list[ModelRequest] = []

        @wrap_model_call
        async def capture(
            request: ModelRequest,
            handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
        ) -> ModelResponse:
            seen.append(request)
            return await handler(request)

        graph = create_agent(
            model=FakeListChatModel(responses=["first", "second", "third"]),
            middleware=[_middleware(), capture],
            checkpointer=InMemorySaver(),
        )
        config = {"configurable": {"thread_id": "reply-retries"}}
        result = await graph.ainvoke({"messages": [HumanMessage(content="what is up")]}, config)

        assert len(seen) == 3
        assert len(seen[0].messages) == 1
        for attempt, request in enumerate(seen[1:], start=1):
            assert len(request.messages) == attempt + 2
            assert isinstance(request.messages[-1], HumanMessage)
            assert [m.content for m in request.messages[:-1]] == ["what is up", "first", "second"][
                : attempt + 1
            ]
        assert [m.content for m in result["messages"]] == ["what is up", "first", "second", "third"]
        assert [m.content for m in _turn_tail(result["messages"])] == ["first", "second", "third"]
        async for snapshot in graph.aget_state_history(config):
            assert [
                m.content
                for m in snapshot.values.get("messages", [])
                if isinstance(m, HumanMessage)
            ] in ([], ["what is up"])
        posted.assert_awaited_once()
        assert posted.await_args.args[:2] == ("third", "final")

    def test_each_run_resolves_its_own_surface(self) -> None:
        middleware = RequireUserReplyMiddleware(
            TOOL, NO_REPLY_TOOL, initial_surface=WEB_REPLY_SURFACE
        )

        assert middleware.before_agent(_state(), _runtime()) == {
            "reply_surface": WEB_REPLY_SURFACE,
            "reply_nudges": 0,
            "reply_nudge_pending": False,
        }
