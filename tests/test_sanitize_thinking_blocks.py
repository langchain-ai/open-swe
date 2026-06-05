from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage

from agent.middleware.sanitize_thinking_blocks import SanitizeThinkingBlocksMiddleware


def _make_request(messages: list[object], model: object | None = None) -> MagicMock:
    request = MagicMock()
    request.model = model or MagicMock(spec=ChatAnthropic)
    request.messages = messages
    return request


class TestSanitizeThinkingBlocksMiddleware:
    def test_drops_empty_thinking_block_for_anthropic(self) -> None:
        message = AIMessage(
            content=[
                {"type": "thinking", "signature": "abc", "thinking": ""},
                {"type": "text", "text": "ok"},
            ]
        )
        request = _make_request([message])
        response = MagicMock()

        def handler(req: object) -> object:
            assert req is request
            return response

        result = SanitizeThinkingBlocksMiddleware().wrap_model_call(request, handler)

        assert result is response
        assert message.content == [{"type": "text", "text": "ok"}]

    def test_preserves_non_empty_thinking_block_for_anthropic(self) -> None:
        thinking_block = {"type": "thinking", "signature": "abc", "thinking": "reasoning"}
        text_block = {"type": "text", "text": "ok"}
        message = AIMessage(content=[thinking_block, text_block])
        request = _make_request([message])

        SanitizeThinkingBlocksMiddleware().wrap_model_call(request, lambda req: MagicMock())

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

        async def handler(req: object) -> object:
            assert req is request
            return response

        result = await SanitizeThinkingBlocksMiddleware().awrap_model_call(request, handler)

        assert result is response
        assert message.content == [{"type": "text", "text": "ok"}]

    def test_ignores_non_anthropic_models(self) -> None:
        thinking_block = {"type": "thinking", "signature": "abc", "thinking": ""}
        message = AIMessage(content=[thinking_block, {"type": "text", "text": "ok"}])
        request = _make_request([message], model=MagicMock())

        SanitizeThinkingBlocksMiddleware().wrap_model_call(request, lambda req: MagicMock())

        assert message.content == [thinking_block, {"type": "text", "text": "ok"}]
