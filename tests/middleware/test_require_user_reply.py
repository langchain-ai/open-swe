from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.middleware.require_user_reply import RequireUserReplyMiddleware

TOOL = "slack_reply"


def _state(*messages: Any) -> AgentState:
    return cast(AgentState, {"messages": list(messages)})


def _reply_call() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": TOOL, "args": {"message": "done"}, "id": "call-1"}],
    )


def _runtime() -> Any:
    return MagicMock()


class TestRequireUserReplyMiddleware:
    @pytest.mark.asyncio
    async def test_reinvokes_when_the_turn_ends_with_undelivered_text(self) -> None:
        middleware = RequireUserReplyMiddleware(TOOL)

        result = await middleware.aafter_model(
            _state(HumanMessage(content="what is up"), AIMessage(content="all good")),
            _runtime(),
        )

        assert result == {"jump_to": "model"}

    @pytest.mark.asyncio
    async def test_lets_the_turn_end_once_the_reply_tool_ran(self) -> None:
        middleware = RequireUserReplyMiddleware(TOOL)

        result = await middleware.aafter_model(
            _state(HumanMessage(content="what is up"), _reply_call(), AIMessage(content="done")),
            _runtime(),
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_leaves_a_turn_that_is_still_calling_tools_alone(self) -> None:
        middleware = RequireUserReplyMiddleware(TOOL)

        result = await middleware.aafter_model(
            _state(
                HumanMessage(content="what is up"),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "execute", "args": {}, "id": "call-2"}],
                ),
            ),
            _runtime(),
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_gives_up_after_the_retry_budget(self) -> None:
        middleware = RequireUserReplyMiddleware(TOOL, max_retries=2)
        state = _state(HumanMessage(content="what is up"), AIMessage(content="all good"))

        assert await middleware.aafter_model(state, _runtime()) == {"jump_to": "model"}
        assert await middleware.aafter_model(state, _runtime()) == {"jump_to": "model"}
        assert await middleware.aafter_model(state, _runtime()) is None

    @pytest.mark.asyncio
    async def test_only_a_pending_retry_carries_the_nudge_into_the_prompt(self) -> None:
        middleware = RequireUserReplyMiddleware(TOOL)
        seen: list[str] = []

        async def handler(request: ModelRequest[None]) -> ModelResponse[Any]:
            system = request.system_message
            seen.append(str(system.content) if system else "")
            return cast(ModelResponse[Any], MagicMock())

        def request() -> ModelRequest[None]:
            def override(**kwargs: Any) -> Any:
                replaced = MagicMock()
                replaced.system_message = kwargs["system_message"]
                return replaced

            built = MagicMock()
            built.system_message = SystemMessage(content="base prompt")
            built.override = override
            return cast(ModelRequest[None], built)

        await middleware.awrap_model_call(request(), handler)
        await middleware.aafter_model(
            _state(HumanMessage(content="what is up"), AIMessage(content="all good")),
            _runtime(),
        )
        await middleware.awrap_model_call(request(), handler)

        assert TOOL not in seen[0]
        assert TOOL in seen[1]
