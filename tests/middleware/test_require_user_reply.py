from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents.middleware.types import AgentState
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.input_messages import message_sender_id
from agent.middleware.require_user_reply import (
    REPLY_GUARD,
    SLACK_REPLY_SURFACE,
    WEB_REPLY_SURFACE,
    RequireUserReplyMiddleware,
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


def _assert_nudged(result: dict[str, Any] | None, attempt: int) -> list[HumanMessage]:
    assert result is not None
    assert result["reply_nudges"] == attempt
    assert result["jump_to"] == "model"
    nudge = result["messages"][-1]
    assert message_sender_id(nudge.content, kind="system") == REPLY_GUARD["id"]
    assert TOOL in nudge.content and NO_REPLY_TOOL in nudge.content
    return cast(list[HumanMessage], result["messages"])


class TestRequireUserReplyMiddleware:
    @pytest.mark.asyncio
    async def test_reinvokes_when_the_turn_ends_with_undelivered_text(self) -> None:
        result = await _middleware().aafter_model(
            _state(HumanMessage(content="what is up"), AIMessage(content="all good")),
            _runtime(),
        )

        _assert_nudged(result, 1)

    @pytest.mark.asyncio
    async def test_a_nudge_does_not_start_a_new_turn(self) -> None:
        """The earlier progress ack must still not count once the nudge is in history."""
        middleware = _middleware()
        messages: list[Any] = [
            HumanMessage(content="what is up"),
            _reply("call-1", "progress"),
            _result("call-1"),
            AIMessage(content="all good"),
        ]
        first = await middleware.aafter_model(_state(*messages), _runtime())
        messages += [*_assert_nudged(first, 1), AIMessage(content="")]

        second = await middleware.aafter_model(_state(*messages, reply_nudges=1), _runtime())

        _assert_nudged(second, 2)

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

        assert result == {"reply_nudges": 0}

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

        _assert_nudged(result, 1)

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

        assert result == {"reply_nudges": 0}

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

        _assert_nudged(result, 1)

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

        assert result is None

    @pytest.mark.asyncio
    async def test_leaves_a_turn_that_is_still_calling_tools_alone(self) -> None:
        result = await _middleware().aafter_model(
            _state(HumanMessage(content="what is up"), _call("execute", "call-2")),
            _runtime(),
        )

        assert result is None

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

        assert result == {"reply_nudges": 0}
        assert posted.await_args.args[0] == "all good"

    @pytest.mark.asyncio
    async def test_blank_replies_to_the_nudges_still_post_the_earlier_answer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import agent.slack.tools.reply as reply_tool

        posted = AsyncMock(return_value={"success": True})
        monkeypatch.setattr(reply_tool, "slack_reply", posted)
        middleware = _middleware(max_retries=2)
        messages: list[Any] = [HumanMessage(content="what is up"), AIMessage(content="all good")]
        for attempt in (1, 2):
            result = await middleware.aafter_model(
                _state(*messages, reply_nudges=attempt - 1), _runtime()
            )
            messages += [*_assert_nudged(result, attempt), AIMessage(content="")]

        await middleware.aafter_model(_state(*messages, reply_nudges=2), _runtime())

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

    def test_each_run_resolves_its_own_surface(self) -> None:
        middleware = RequireUserReplyMiddleware(
            TOOL, NO_REPLY_TOOL, initial_surface=WEB_REPLY_SURFACE
        )

        assert middleware.before_agent(_state(), _runtime()) == {
            "reply_surface": WEB_REPLY_SURFACE,
            "reply_nudges": 0,
        }
