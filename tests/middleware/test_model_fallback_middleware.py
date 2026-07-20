"""Tests for ModelFallbackMiddleware."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import anthropic
import httpx
import openai
import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
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


def _anthropic_model_not_available_error() -> anthropic.BadRequestError:
    body = {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "In order to access this model, your organization or workspace must have data retention enabled.",
            "details": {"error_code": "model_not_available"},
        },
        "request_id": "req_test",
    }
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(400, request=request, json=body)
    return anthropic.BadRequestError("model unavailable", response=response, body=body)


def _make_request() -> ModelRequest[None]:
    request = MagicMock()
    request.override = MagicMock(return_value=MagicMock(name="overridden_request"))
    return cast(ModelRequest[None], request)


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

    def test_httpx_remote_protocol_error_falls_back(self) -> None:
        exc = httpx.RemoteProtocolError(
            "peer closed connection without sending complete message body (incomplete chunked read)"
        )
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

        async def handler(req: ModelRequest[None]) -> ModelResponse[Any]:
            calls.append(req)
            if len(calls) == 1:
                raise _anthropic_overloaded()
            return cast(ModelResponse[Any], good_response)

        request = _make_request()
        result = await middleware.awrap_model_call(request, handler)

        assert result is good_response
        assert len(calls) == 2
        override = cast(MagicMock, request.override)
        override.assert_called_once_with(model=fallback_model)
        assert calls[1] is override.return_value

    @pytest.mark.asyncio
    async def test_async_falls_over_on_stream_transport_error(self) -> None:
        fallback_model = MagicMock(name="fallback_model")
        middleware = ModelFallbackMiddleware(fallback_model)

        calls: list[object] = []
        good_response = MagicMock(result=[AIMessage(content="ok from fallback")])

        async def handler(req: ModelRequest[None]) -> ModelResponse[Any]:
            calls.append(req)
            if len(calls) == 1:
                raise httpx.RemoteProtocolError(
                    "peer closed connection without sending complete message body "
                    "(incomplete chunked read)"
                )
            return cast(ModelResponse[Any], good_response)

        request = _make_request()
        result = await middleware.awrap_model_call(request, handler)

        assert result is good_response
        assert len(calls) == 2
        override = cast(MagicMock, request.override)
        override.assert_called_once_with(model=fallback_model)
        assert calls[1] is override.return_value

    @pytest.mark.asyncio
    async def test_async_propagates_non_transient_error(self) -> None:
        middleware = ModelFallbackMiddleware(MagicMock())
        calls: list[object] = []

        async def handler(req: ModelRequest[None]) -> ModelResponse[Any]:
            calls.append(req)
            raise ValueError("not transient")

        with pytest.raises(ValueError, match="not transient"):
            await middleware.awrap_model_call(_make_request(), handler)

        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_async_surfaces_model_unavailable_error(self) -> None:
        middleware = ModelFallbackMiddleware(MagicMock())

        async def handler(_req: ModelRequest[None]) -> ModelResponse[Any]:
            raise _anthropic_model_not_available_error()

        result = await middleware.awrap_model_call(_make_request(), handler)

        assert isinstance(result, AIMessage)
        assert "selected Anthropic model is not available" in result.text
        assert "data retention enabled" in result.text

    @pytest.mark.asyncio
    async def test_async_retries_primary_after_fallback_failure(self) -> None:
        """If the fallback also fails transiently, retry the primary instead of crashing."""
        fallback_model = MagicMock(name="fallback_model")
        middleware = ModelFallbackMiddleware(fallback_model, backoff_schedule=(0.0, 0.0, 0.0))
        calls: list[object] = []
        good_response = MagicMock(result=[AIMessage(content="ok from primary retry")])

        async def handler(req: ModelRequest[None]) -> ModelResponse[Any]:
            calls.append(req)
            if len(calls) <= 2:  # primary fails, then fallback fails
                raise _openai_5xx()
            return cast(ModelResponse[Any], good_response)

        request = _make_request()
        result = await middleware.awrap_model_call(request, handler)

        assert result is good_response
        assert len(calls) == 3
        # Attempts alternate primary -> fallback -> primary.
        assert calls[0] is request
        assert calls[1] is cast(MagicMock, request.override).return_value
        assert calls[2] is request

    @pytest.mark.asyncio
    async def test_async_exhaustion_returns_outage_message(self) -> None:
        """After exhausting all attempts, the run ends with a visible message, not a crash."""
        middleware = ModelFallbackMiddleware(MagicMock(), backoff_schedule=(0.0, 0.0))
        calls: list[object] = []

        async def handler(req: ModelRequest[None]) -> ModelResponse[Any]:
            calls.append(req)
            raise _openai_5xx()

        result = await middleware.awrap_model_call(_make_request(), handler)

        assert len(calls) == 3
        assert isinstance(result, AIMessage)
        assert "retrigger" in result.text

    @pytest.mark.asyncio
    async def test_async_exhaustion_raises_when_message_disabled(self) -> None:
        middleware = ModelFallbackMiddleware(
            MagicMock(), backoff_schedule=(0.0,), surface_outage_message=False
        )
        calls: list[object] = []

        async def handler(req: ModelRequest[None]) -> ModelResponse[Any]:
            calls.append(req)
            raise _openai_5xx()

        with pytest.raises(openai.APIStatusError):
            await middleware.awrap_model_call(_make_request(), handler)

        assert len(calls) == 2
