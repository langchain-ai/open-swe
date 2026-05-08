"""Tests for ModelFallbackMiddleware."""

from __future__ import annotations

from unittest.mock import MagicMock

import anthropic
import httpx
import openai
import pytest
from langchain_core.messages import AIMessage

from agent.middleware.model_fallback import (
    ModelFallbackMiddleware,
    _should_fallback,
)


def _anthropic_overloaded() -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(
        529,
        request=request,
        json={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
    )
    body = response.json()
    return anthropic.APIStatusError("Overloaded", response=response, body=body)


def _openai_5xx() -> openai.APIStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(503, request=request, json={"error": {"message": "unavailable"}})
    return openai.APIStatusError("unavailable", response=response, body=response.json())


def _make_request() -> MagicMock:
    request = MagicMock()
    request.override = MagicMock(return_value=MagicMock(name="overridden_request"))
    return request


class TestShouldFallback:
    def test_anthropic_529_overload_falls_back(self) -> None:
        assert _should_fallback(_anthropic_overloaded()) is True

    def test_openai_503_falls_back(self) -> None:
        assert _should_fallback(_openai_5xx()) is True

    def test_anthropic_rate_limit_falls_back(self) -> None:
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx.Response(429, request=request, json={"error": {}})
        exc = anthropic.RateLimitError("rate", response=response, body={})
        assert _should_fallback(exc) is True

    def test_anthropic_400_does_not_fall_back(self) -> None:
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx.Response(400, request=request, json={"error": {}})
        exc = anthropic.BadRequestError("bad", response=response, body={})
        assert _should_fallback(exc) is False

    def test_value_error_does_not_fall_back(self) -> None:
        assert _should_fallback(ValueError("nope")) is False


class TestModelFallbackMiddleware:
    @pytest.mark.asyncio
    async def test_async_falls_over_on_overloaded(self) -> None:
        fallback_model = MagicMock(name="fallback_model")
        middleware = ModelFallbackMiddleware(fallback_model)

        calls: list[object] = []
        good_response = MagicMock(result=[AIMessage(content="ok from fallback")])

        async def handler(req: object) -> object:
            calls.append(req)
            if len(calls) == 1:
                raise _anthropic_overloaded()
            return good_response

        request = _make_request()
        result = await middleware.awrap_model_call(request, handler)

        assert result is good_response
        assert len(calls) == 2
        request.override.assert_called_once_with(model=fallback_model)
        assert calls[1] is request.override.return_value

    @pytest.mark.asyncio
    async def test_async_propagates_non_transient_error(self) -> None:
        middleware = ModelFallbackMiddleware(MagicMock())
        calls: list[object] = []

        async def handler(req: object) -> object:
            calls.append(req)
            raise ValueError("not transient")

        with pytest.raises(ValueError, match="not transient"):
            await middleware.awrap_model_call(_make_request(), handler)

        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_async_does_not_double_fall_back(self) -> None:
        """If the fallback also fails transiently, the error propagates."""
        middleware = ModelFallbackMiddleware(MagicMock())
        calls: list[object] = []

        async def handler(req: object) -> object:
            calls.append(req)
            raise _openai_5xx()

        with pytest.raises(openai.APIStatusError):
            await middleware.awrap_model_call(_make_request(), handler)

        assert len(calls) == 2

    def test_sync_falls_over_on_overloaded(self) -> None:
        fallback_model = MagicMock(name="fallback_model")
        middleware = ModelFallbackMiddleware(fallback_model)
        calls: list[object] = []
        good_response = MagicMock(result=[AIMessage(content="ok")])

        def handler(req: object) -> object:
            calls.append(req)
            if len(calls) == 1:
                raise _anthropic_overloaded()
            return good_response

        request = _make_request()
        result = middleware.wrap_model_call(request, handler)

        assert result is good_response
        assert len(calls) == 2
        request.override.assert_called_once_with(model=fallback_model)
