from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, ToolCall

from agent.middleware.sanitize_thinking_blocks import SanitizeThinkingBlocksMiddleware


def _make_request(messages: list[object], model: object | None = None) -> ModelRequest[None]:
    request = MagicMock()
    request.model = model or MagicMock(spec=ChatAnthropic)
    request.messages = messages
    return cast(ModelRequest[None], request)


async def _noop_handler(_req: ModelRequest[None]) -> ModelResponse[Any]:
    return cast(ModelResponse[Any], MagicMock())


class TestSanitizeThinkingBlocksMiddleware:
    @pytest.mark.asyncio
    async def test_drops_empty_thinking_block_for_anthropic(self) -> None:
        message = AIMessage(
            content=[
                {"type": "thinking", "signature": "abc", "thinking": ""},
                {"type": "text", "text": "ok"},
            ]
        )
        request = _make_request([message])
        response = MagicMock()

        async def handler(req: ModelRequest[None]) -> ModelResponse[Any]:
            assert req is request.override.return_value
            return cast(ModelResponse[Any], response)

        result = await SanitizeThinkingBlocksMiddleware().awrap_model_call(request, handler)

        assert result is response
        assert message.content == [{"type": "text", "text": "ok"}]
        request.override.assert_called_once_with(messages=[])

    @pytest.mark.asyncio
    async def test_preserves_non_empty_thinking_block_for_anthropic(self) -> None:
        thinking_block = {"type": "thinking", "signature": "abc", "thinking": "reasoning"}
        text_block = {"type": "text", "text": "ok"}
        message = AIMessage(content=[thinking_block, text_block])
        trailing_message = HumanMessage(content="hi")
        request = _make_request([message, trailing_message])

        await SanitizeThinkingBlocksMiddleware().awrap_model_call(request, _noop_handler)

        assert message.content == [thinking_block, text_block]
        request.override.assert_called_once_with(messages=[message, trailing_message])

    @pytest.mark.asyncio
    async def test_async_drops_missing_thinking_block_for_anthropic(self) -> None:
        message = AIMessage(
            content=[
                {"type": "thinking", "signature": "abc"},
                {"type": "text", "text": "ok"},
            ]
        )
        request = _make_request([HumanMessage(content="hi"), message])
        response = MagicMock()

        async def handler(req: ModelRequest[None]) -> ModelResponse[Any]:
            assert req is request.override.return_value
            return cast(ModelResponse[Any], response)

        result = await SanitizeThinkingBlocksMiddleware().awrap_model_call(request, handler)

        assert result is response
        assert message.content == [{"type": "text", "text": "ok"}]
        request.override.assert_called_once_with(messages=[HumanMessage(content="hi")])

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "content",
        [
            [{"type": "thinking", "thinking": "", "signature": "abc"}],
            "assistant prefill",
        ],
    )
    async def test_drops_trailing_assistant_prefill_for_anthropic(self, content: object) -> None:
        trailing_message = AIMessage(content=content)
        request = _make_request([HumanMessage(content="hi"), trailing_message])

        await SanitizeThinkingBlocksMiddleware().awrap_model_call(request, _noop_handler)

        request.override.assert_called_once_with(messages=[HumanMessage(content="hi")])

    @pytest.mark.asyncio
    async def test_preserves_trailing_assistant_message_with_tool_calls(self) -> None:
        tool_call = ToolCall(name="search", args={"query": "test"}, id="call_1")
        message = AIMessage(content="", tool_calls=[tool_call])
        request = _make_request([HumanMessage(content="hi"), message])

        await SanitizeThinkingBlocksMiddleware().awrap_model_call(request, _noop_handler)

        request.override.assert_called_once_with(messages=[HumanMessage(content="hi"), message])

    @pytest.mark.asyncio
    async def test_ignores_non_anthropic_models(self) -> None:
        thinking_block = {"type": "thinking", "signature": "abc", "thinking": ""}
        message = AIMessage(content=[thinking_block, {"type": "text", "text": "ok"}])
        request = _make_request([message], model=MagicMock())

        await SanitizeThinkingBlocksMiddleware().awrap_model_call(request, _noop_handler)

        assert message.content == [thinking_block, {"type": "text", "text": "ok"}]
