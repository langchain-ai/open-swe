from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.middleware.sanitize_fireworks_messages import SanitizeFireworksMessagesMiddleware


def _make_request(messages: list[object], model: object | None = None) -> ModelRequest[None]:
    request = MagicMock()
    request.model = model
    request.messages = messages
    return cast(ModelRequest[None], request)


def _fireworks_model() -> MagicMock:
    """A mock that satisfies ``_is_chat_fireworks`` via spec'd ``ChatFireworks``."""
    try:
        from langchain_fireworks.chat_models import ChatFireworks
    except ImportError:  # pragma: no cover
        pytest.skip("langchain-fireworks not installed")
    return MagicMock(spec=ChatFireworks)


async def _noop_handler(_req: ModelRequest[None]) -> ModelResponse[Any]:
    return cast(ModelResponse[Any], MagicMock())


class TestSanitizeFireworksMessagesMiddleware:
    @pytest.mark.asyncio
    async def test_drops_legacy_function_call(self) -> None:
        tool_result = ToolMessage(content="result", tool_call_id="tc1")
        message = AIMessage(
            content="",
            tool_calls=[{"name": "read_file", "args": {"file_path": "/x"}, "id": "tc1"}],
            additional_kwargs={"function_call": {"name": "read_file", "arguments": "{}"}},
        )
        request = _make_request(
            [HumanMessage(content="hi"), message, tool_result],
            model=_fireworks_model(),
        )
        response = MagicMock()

        async def handler(req: ModelRequest[None]) -> ModelResponse[Any]:
            assert req is request
            return cast(ModelResponse[Any], response)

        result = await SanitizeFireworksMessagesMiddleware().awrap_model_call(request, handler)

        assert result is response
        assert "function_call" not in message.additional_kwargs
        # tool_calls are untouched
        assert len(message.tool_calls) == 1

    @pytest.mark.asyncio
    async def test_preserves_message_without_function_call(self) -> None:
        message = AIMessage(
            content="ok",
            tool_calls=[{"name": "read_file", "args": {"file_path": "/x"}, "id": "tc1"}],
        )
        request = _make_request([message], model=_fireworks_model())

        await SanitizeFireworksMessagesMiddleware().awrap_model_call(request, _noop_handler)

        assert "function_call" not in message.additional_kwargs
        assert len(message.tool_calls) == 1

    @pytest.mark.asyncio
    async def test_drops_function_call_with_no_tool_calls(self) -> None:
        message = AIMessage(
            content="",
            additional_kwargs={"function_call": {"name": "search", "arguments": "{}"}},
        )
        request = _make_request([message], model=_fireworks_model())

        await SanitizeFireworksMessagesMiddleware().awrap_model_call(request, _noop_handler)

        assert "function_call" not in message.additional_kwargs

    @pytest.mark.asyncio
    async def test_ignores_non_fireworks_models(self) -> None:
        message = AIMessage(
            content="",
            tool_calls=[{"name": "read_file", "args": {"file_path": "/x"}, "id": "tc1"}],
            additional_kwargs={"function_call": {"name": "read_file", "arguments": "{}"}},
        )
        # Non-Fireworks model (plain MagicMock, no ChatFireworks in its spec chain)
        request = _make_request([message], model=MagicMock())

        await SanitizeFireworksMessagesMiddleware().awrap_model_call(request, _noop_handler)

        # function_call preserved for non-Fireworks providers
        assert "function_call" in message.additional_kwargs

    @pytest.mark.asyncio
    async def test_skips_non_ai_messages(self) -> None:
        messages = [
            HumanMessage(content="hi"),
            ToolMessage(content="result", tool_call_id="tc1"),
        ]
        request = _make_request(messages, model=_fireworks_model())

        await SanitizeFireworksMessagesMiddleware().awrap_model_call(request, _noop_handler)

        # No AIMessages to mutate — handler still called
        assert all(not isinstance(m, AIMessage) for m in messages)
