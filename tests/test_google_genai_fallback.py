"""Tests for google_genai fallback mapping and middleware error handling.

Covers the gap exposed by the production incident where seven reviewer runs
configured with ``google_genai:gemini-3.5-flash`` errored on
``ChatGoogleGenerativeAIError ... RESOURCE_EXHAUSTED (429)`` with no
cross-provider fallback engaging.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

from agent.middleware.model_fallback import (
    ModelFallbackMiddleware,
    _is_google_genai_transient,
    _should_fallback,
)
from agent.utils.model import fallback_model_id_for


class ChatGoogleGenerativeAIError(Exception):
    """Stand-in for ``langchain_google_genai.ChatGoogleGenerativeAIError``.

    The real class isn't installed in the test image; the middleware detects
    these errors structurally by type name + message content so a synthetic
    class with the same name exercises the same code path.
    """


class TestFallbackModelIdFor:
    def test_google_genai_primary_falls_back_to_anthropic(self) -> None:
        assert (
            fallback_model_id_for("google_genai:gemini-3.5-flash")
            == "anthropic:claude-opus-4-5"
        )

    def test_google_genai_pro_primary_falls_back_to_anthropic(self) -> None:
        assert (
            fallback_model_id_for("google_genai:gemini-2.5-pro")
            == "anthropic:claude-opus-4-5"
        )

    def test_anthropic_mapping_unchanged(self) -> None:
        assert fallback_model_id_for("anthropic:claude-opus-4-5") == "openai:gpt-5.5"

    def test_openai_mapping_unchanged(self) -> None:
        assert (
            fallback_model_id_for("openai:gpt-5.5") == "anthropic:claude-opus-4-5"
        )

    def test_unknown_provider_returns_none(self) -> None:
        assert fallback_model_id_for("ollama:llama3") is None


class TestGoogleGenAITransientDetection:
    def test_resource_exhausted_429_is_transient(self) -> None:
        exc = ChatGoogleGenerativeAIError(
            "429 RESOURCE_EXHAUSTED: Quota exceeded for quota metric ..."
        )
        assert _is_google_genai_transient(exc) is True
        assert _should_fallback(exc) is True

    def test_429_without_resource_exhausted_text_is_transient(self) -> None:
        exc = ChatGoogleGenerativeAIError("Got 429 from upstream")
        assert _should_fallback(exc) is True

    def test_503_unavailable_is_transient(self) -> None:
        exc = ChatGoogleGenerativeAIError("503 UNAVAILABLE: backend overloaded")
        assert _should_fallback(exc) is True

    def test_400_invalid_argument_is_not_transient(self) -> None:
        exc = ChatGoogleGenerativeAIError("400 INVALID_ARGUMENT: bad request")
        assert _is_google_genai_transient(exc) is False
        assert _should_fallback(exc) is False

    def test_unrelated_exception_is_not_transient(self) -> None:
        assert _is_google_genai_transient(ValueError("RESOURCE_EXHAUSTED")) is False


class TestMiddlewareFallsBackOnGoogleGenAIQuota:
    @pytest.mark.asyncio
    async def test_async_falls_over_on_resource_exhausted(self) -> None:
        fallback_model = MagicMock(name="fallback_model")
        middleware = ModelFallbackMiddleware(fallback_model)

        calls: list[object] = []
        good_response = MagicMock(result=[AIMessage(content="ok from fallback")])

        async def handler(req: object) -> object:
            calls.append(req)
            if len(calls) == 1:
                raise ChatGoogleGenerativeAIError(
                    "429 RESOURCE_EXHAUSTED: Quota exceeded for "
                    "generativelanguage.googleapis.com/generate_content_free_tier_requests"
                )
            return good_response

        request = MagicMock()
        request.override = MagicMock(return_value=MagicMock(name="overridden"))

        result = await middleware.awrap_model_call(request, handler)

        assert result is good_response
        assert len(calls) == 2
        request.override.assert_called_once_with(model=fallback_model)
        assert calls[1] is request.override.return_value

    def test_sync_falls_over_on_resource_exhausted(self) -> None:
        fallback_model = MagicMock(name="fallback_model")
        middleware = ModelFallbackMiddleware(fallback_model)

        calls: list[object] = []
        good_response = MagicMock(result=[AIMessage(content="ok from fallback")])

        def handler(req: object) -> object:
            calls.append(req)
            if len(calls) == 1:
                raise ChatGoogleGenerativeAIError(
                    "429 RESOURCE_EXHAUSTED: gemini quota exhausted"
                )
            return good_response

        request = MagicMock()
        request.override = MagicMock(return_value=MagicMock(name="overridden"))

        result = middleware.wrap_model_call(request, handler)

        assert result is good_response
        assert len(calls) == 2
        request.override.assert_called_once_with(model=fallback_model)
