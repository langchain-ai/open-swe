"""Middleware that falls back to a secondary model when the primary fails transiently.

Wraps the model call. When the primary model raises a transient provider error
(5xx, 429, connection/timeout), the same request is retried once against the
configured fallback model. The fallback is bound to tools by the agent factory
on the second call, so swapping ``request.model`` is sufficient.

Bidirectional: if the primary is Anthropic the fallback is typically OpenAI,
and vice versa. The middleware itself is provider-agnostic — it inspects the
exception type/status code to decide whether to fall over.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import anthropic
import openai
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504, 529}

_TRANSIENT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.InternalServerError,
)

# google-genai errors are detected by type-name + message string so we don't
# require ``langchain_google_genai`` at import time. ``ChatGoogleGenerativeAIError``
# wraps upstream HTTP errors; for quota / rate-limit cases the message contains
# ``RESOURCE_EXHAUSTED`` or the HTTP status ``429``.
_GOOGLE_GENAI_ERROR_TYPE_NAMES = frozenset(
    {
        "ChatGoogleGenerativeAIError",
        "GoogleGenerativeAIError",
        "ResourceExhausted",
    }
)
_GOOGLE_GENAI_RETRYABLE_MESSAGE_TOKENS = (
    "RESOURCE_EXHAUSTED",
    "429",
    "quota",
    "rate limit",
    "503",
    "UNAVAILABLE",
    "500",
    "INTERNAL",
    "504",
    "DEADLINE_EXCEEDED",
)


def _is_google_genai_transient(exc: BaseException) -> bool:
    """Detect retryable google-genai errors without importing the SDK."""
    type_name = type(exc).__name__
    if type_name not in _GOOGLE_GENAI_ERROR_TYPE_NAMES:
        # Walk the MRO so subclasses are caught too (e.g. provider-specific
        # subclasses of ChatGoogleGenerativeAIError).
        if not any(
            base.__name__ in _GOOGLE_GENAI_ERROR_TYPE_NAMES for base in type(exc).__mro__
        ):
            return False
    message = str(exc)
    return any(token in message for token in _GOOGLE_GENAI_RETRYABLE_MESSAGE_TOKENS)


def _should_fallback(exc: BaseException) -> bool:
    if isinstance(exc, _TRANSIENT_EXCEPTIONS):
        return True
    # Catches OverloadedError (529) and other 5xx/429 surfaced as APIStatusError.
    if isinstance(exc, (anthropic.APIStatusError, openai.APIStatusError)):
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and status in _RETRYABLE_STATUS_CODES:
            return True
    # google-genai surfaces 429 / RESOURCE_EXHAUSTED as ChatGoogleGenerativeAIError
    # with the status code embedded in the message rather than as a typed
    # attribute, so match by type name + message.
    if _is_google_genai_transient(exc):
        return True
    return False


class ModelFallbackMiddleware(AgentMiddleware):
    """Retry the model call against a fallback provider on transient errors."""

    def __init__(self, fallback_model: BaseChatModel) -> None:
        super().__init__()
        self._fallback_model = fallback_model

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        try:
            return handler(request)
        except Exception as exc:
            if not _should_fallback(exc):
                raise
            logger.warning(
                "Primary model failed (%s); falling back to %s",
                type(exc).__name__,
                getattr(self._fallback_model, "model_name", None)
                or getattr(self._fallback_model, "model", "fallback"),
            )
            return handler(request.override(model=self._fallback_model))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        try:
            return await handler(request)
        except Exception as exc:
            if not _should_fallback(exc):
                raise
            logger.warning(
                "Primary model failed (%s); falling back to %s",
                type(exc).__name__,
                getattr(self._fallback_model, "model_name", None)
                or getattr(self._fallback_model, "model", "fallback"),
            )
            return await handler(request.override(model=self._fallback_model))
