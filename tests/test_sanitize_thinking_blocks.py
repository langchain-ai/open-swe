"""Tests for SanitizeThinkingBlocksMiddleware."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.middleware.sanitize_thinking_blocks import SanitizeThinkingBlocksMiddleware


class _FakeChatAnthropic:
    """Stand-in whose class name matches the ChatAnthropic check."""


_FakeChatAnthropic.__name__ = "ChatAnthropic"


class _FakeChatOpenAI:
    pass


_FakeChatOpenAI.__name__ = "ChatOpenAI"


def _make_request(model: object, messages: list) -> MagicMock:
    request = MagicMock()
    request.model = model
    request.messages = messages
    return request


class TestSanitizeThinkingBlocksMiddleware:
    @pytest.mark.asyncio
    async def test_drops_empty_thinking_block(self) -> None:
        ai = AIMessage(
            content=[
                {"type": "thinking", "signature": "abc", "thinking": ""},
                {"type": "text", "text": "ok"},
            ]
        )
        request = _make_request(_FakeChatAnthropic(), [HumanMessage(content="hi"), ai])

        async def handler(req: object) -> str:
            return "done"

        middleware = SanitizeThinkingBlocksMiddleware()
        result = await middleware.awrap_model_call(request, handler)

        assert result == "done"
        assert ai.content == [{"type": "text", "text": "ok"}]

    @pytest.mark.asyncio
    async def test_preserves_non_empty_thinking_block(self) -> None:
        ai = AIMessage(
            content=[
                {"type": "thinking", "signature": "abc", "thinking": "reasoning..."},
                {"type": "text", "text": "ok"},
            ]
        )
        request = _make_request(_FakeChatAnthropic(), [ai])

        async def handler(req: object) -> str:
            return "done"

        middleware = SanitizeThinkingBlocksMiddleware()
        await middleware.awrap_model_call(request, handler)

        assert ai.content == [
            {"type": "thinking", "signature": "abc", "thinking": "reasoning..."},
            {"type": "text", "text": "ok"},
        ]

    @pytest.mark.asyncio
    async def test_skips_non_anthropic_model(self) -> None:
        ai = AIMessage(
            content=[
                {"type": "thinking", "signature": "abc", "thinking": ""},
                {"type": "text", "text": "ok"},
            ]
        )
        original = list(ai.content)
        request = _make_request(_FakeChatOpenAI(), [ai])

        async def handler(req: object) -> str:
            return "done"

        middleware = SanitizeThinkingBlocksMiddleware()
        await middleware.awrap_model_call(request, handler)

        assert ai.content == original

    def test_sync_drops_empty_thinking_block(self) -> None:
        ai = AIMessage(
            content=[
                {"type": "thinking", "signature": "abc", "thinking": ""},
                {"type": "text", "text": "ok"},
            ]
        )
        request = _make_request(_FakeChatAnthropic(), [ai])

        def handler(req: object) -> str:
            return "done"

        middleware = SanitizeThinkingBlocksMiddleware()
        result = middleware.wrap_model_call(request, handler)

        assert result == "done"
        assert ai.content == [{"type": "text", "text": "ok"}]
