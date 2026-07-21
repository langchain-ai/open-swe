from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage

from agent.middleware.sanitize_thinking_blocks import SanitizeThinkingBlocksMiddleware


def _make_request(messages: list[object], model: object | None = None) -> ModelRequest[None]:
    request = MagicMock()
    request.model = model or MagicMock(spec=ChatAnthropic)
    request.messages = messages
    return cast(ModelRequest[None], request)


async def _noop_handler(_req: ModelRequest[None]) -> ModelResponse[Any]:
    return cast(ModelResponse[Any], MagicMock())


def _response_handler(
    messages: list[object],
) -> Callable[[ModelRequest[None]], Awaitable[ModelResponse[Any]]]:
    response = MagicMock()
    response.result = messages

    async def handler(_req: ModelRequest[None]) -> ModelResponse[Any]:
        return cast(ModelResponse[Any], response)

    return handler


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
            assert req is request
            return cast(ModelResponse[Any], response)

        result = await SanitizeThinkingBlocksMiddleware().awrap_model_call(request, handler)

        assert result is response
        assert message.content == [{"type": "text", "text": "ok"}]

    @pytest.mark.asyncio
    async def test_preserves_non_empty_thinking_block_for_anthropic(self) -> None:
        thinking_block = {"type": "thinking", "signature": "abc", "thinking": "reasoning"}
        text_block = {"type": "text", "text": "ok"}
        message = AIMessage(content=[thinking_block, text_block])
        request = _make_request([message])

        await SanitizeThinkingBlocksMiddleware().awrap_model_call(request, _noop_handler)

        assert message.content == [thinking_block, text_block]

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
            assert req is request
            return cast(ModelResponse[Any], response)

        result = await SanitizeThinkingBlocksMiddleware().awrap_model_call(request, handler)

        assert result is response
        assert message.content == [{"type": "text", "text": "ok"}]

    @pytest.mark.asyncio
    async def test_strips_serialized_reasoning_prefix_from_response(self) -> None:
        final = AIMessage(content='{"reasoning":"","type":"reasoning"}Here is the review summary.')
        request = _make_request([HumanMessage(content="review")])
        handler = _response_handler([final])

        await SanitizeThinkingBlocksMiddleware().awrap_model_call(request, handler)

        assert final.content == "Here is the review summary."

    @pytest.mark.asyncio
    async def test_preserves_legitimate_json_review_content(self) -> None:
        review_json = '{"reasoning":"non-empty","type":"reasoning"} and a summary'
        final = AIMessage(content=review_json)
        request = _make_request([HumanMessage(content="review")])
        handler = _response_handler([final])

        await SanitizeThinkingBlocksMiddleware().awrap_model_call(request, handler)

        assert final.content == review_json

    @pytest.mark.asyncio
    async def test_ignores_non_anthropic_models(self) -> None:
        thinking_block = {"type": "thinking", "signature": "abc", "thinking": ""}
        message = AIMessage(content=[thinking_block, {"type": "text", "text": "ok"}])
        request = _make_request([message], model=MagicMock())

        await SanitizeThinkingBlocksMiddleware().awrap_model_call(request, _noop_handler)

        assert message.content == [thinking_block, {"type": "text", "text": "ok"}]
