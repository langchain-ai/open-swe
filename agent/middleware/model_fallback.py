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
from langchain_core.messages import AIMessage

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


def _should_fallback(exc: BaseException) -> bool:
    if isinstance(exc, _TRANSIENT_EXCEPTIONS):
        return True
    # Catches OverloadedError (529) and other 5xx/429 surfaced as APIStatusError.
    if isinstance(exc, (anthropic.APIStatusError, openai.APIStatusError)):
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and status in _RETRYABLE_STATUS_CODES:
            return True
    return False


def _error_body(exc: BaseException) -> dict[str, Any]:
    body = getattr(exc, "body", None)
    return body if isinstance(body, dict) else {}


def _nested_str(data: dict[str, Any], *keys: str) -> str | None:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, str) and current else None


def _provider_access_error_message(exc: BaseException) -> str | None:
    if isinstance(exc, anthropic.BadRequestError):
        body = _error_body(exc)
        error_code = _nested_str(body, "error", "details", "error_code")
        if error_code == "model_not_available":
            provider_message = _nested_str(body, "error", "message") or str(exc)
            return (
                "The selected Anthropic model is not available to this workspace. "
                f"Anthropic returned: {provider_message} "
                "Choose a different model or update the workspace's Anthropic access and retry."
            )

    if isinstance(exc, (openai.BadRequestError, openai.NotFoundError)):
        body = _error_body(exc)
        error_code = _nested_str(body, "error", "code")
        if error_code in {"model_not_found", "model_not_available"}:
            provider_message = _nested_str(body, "error", "message") or str(exc)
            return (
                "The selected OpenAI model is not available to this workspace. "
                f"OpenAI returned: {provider_message} "
                "Choose a different model or update the workspace's OpenAI access and retry."
            )

    return None


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
            access_error_message = _provider_access_error_message(exc)
            if access_error_message is not None:
                logger.warning("Model access error surfaced to user: %s", type(exc).__name__)
                return AIMessage(content=access_error_message)
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
            access_error_message = _provider_access_error_message(exc)
            if access_error_message is not None:
                logger.warning("Model access error surfaced to user: %s", type(exc).__name__)
                return AIMessage(content=access_error_message)
            if not _should_fallback(exc):
                raise
            logger.warning(
                "Primary model failed (%s); falling back to %s",
                type(exc).__name__,
                getattr(self._fallback_model, "model_name", None)
                or getattr(self._fallback_model, "model", "fallback"),
            )
            return await handler(request.override(model=self._fallback_model))
