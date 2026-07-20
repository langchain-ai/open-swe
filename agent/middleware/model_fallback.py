"""Middleware that retries model calls across a primary and fallback provider.

Wraps the model call. When a model raises a transient provider error (5xx,
429, connection/timeout), the request is retried, alternating between the
primary and the configured fallback model with exponential backoff between
attempts. The fallback is bound to tools by the agent factory on each call,
so swapping ``request.model`` is sufficient.

Why alternate with backoff instead of failing over once: both providers can
be routed through the same LLM Gateway, so a gateway outage takes out the
"cross-provider" fallback too. A single immediate failover cannot ride out
even a short shared outage (the gateway's 502 page literally says "try again
in 30 seconds"), and an unprotected fallback call crashes the whole run.
Alternating with a backoff schedule that reaches past 30s lets a long-running
agent run survive multi-minute provider or gateway blips.

Bidirectional: if the primary is Anthropic the fallback is typically OpenAI,
and vice versa. The middleware itself is provider-agnostic — it inspects the
exception type/status code to decide whether an attempt is retryable.

If every attempt fails, the middleware either raises the last error or (by
default) returns a terminal ``AIMessage`` explaining the outage, so the run
ends with a visible message in Slack/GitHub instead of an abrupt crash. The
turn's progress is checkpointed, so the user can retrigger to continue.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import anthropic
import httpx
import openai
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
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
    httpx.TransportError,
)

# Seconds slept before each retry attempt (attempt 0 is the initial call).
# The first failover is immediate: a provider-specific outage should not delay
# the cross-provider retry. Later delays grow past the ~30s the gateway's 502
# page asks for. Each attempt additionally benefits from the SDK's own
# ``max_retries`` backoff, so worst-case wall time before giving up is a few
# minutes — acceptable for a long-running agent, far better than crashing.
DEFAULT_BACKOFF_SCHEDULE: tuple[float, ...] = (0.0, 5.0, 15.0, 30.0, 45.0)

MODEL_OUTAGE_MESSAGE = (
    "I wasn't able to reach the language model providers after several retries "
    "(both the primary and fallback models returned transient errors, e.g. "
    "502/503/overloaded). This is a temporary provider or gateway outage, not a "
    "problem with your task. My progress so far has been saved — please retrigger "
    "the run in a few minutes to continue."
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
    # Gateway policy blocks (spend/PII/secrets guard) are non-retryable 4xx
    # rejections: retrying re-triggers the same policy, so surface them rather
    # than routing through _should_fallback (which would re-raise into a crash).
    if isinstance(exc, (anthropic.APIStatusError, openai.APIStatusError)):
        text = str(exc)
        if "blocked by gateway policies" in text:
            return (
                "This model request was blocked by an LLM Gateway policy "
                "(spend, PII, or secrets guard). This is a policy/config issue, "
                "not a problem with your task. Ask a workspace admin to review the "
                f"gateway policy, then retrigger the run. Details: {text}"
            )

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
    """Retry the model call across primary and fallback providers on transient errors.

    Args:
        fallback_model: Cross-provider model used on odd-numbered attempts.
        backoff_schedule: Seconds slept before each retry. ``len(schedule) + 1``
            is the total number of attempts. Delays get ±25% jitter.
        surface_outage_message: When all attempts fail, return a terminal
            ``AIMessage`` describing the outage instead of raising, so the run
            ends gracefully with a user-visible message rather than a crash.
            Set to ``False`` to re-raise the last error (e.g. if platform-level
            alerting keys off failed runs).
    """

    def __init__(
        self,
        fallback_model: BaseChatModel,
        *,
        backoff_schedule: Sequence[float] = DEFAULT_BACKOFF_SCHEDULE,
        surface_outage_message: bool = True,
    ) -> None:
        super().__init__()
        self._fallback_model = fallback_model
        self._backoff_schedule = tuple(backoff_schedule)
        self._surface_outage_message = surface_outage_message

    def _fallback_name(self) -> str:
        return (
            getattr(self._fallback_model, "model_name", None)
            or getattr(self._fallback_model, "model", None)
            or "fallback"
        )

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        total_attempts = len(self._backoff_schedule) + 1
        last_exc: BaseException | None = None

        for attempt in range(total_attempts):
            # Alternate: primary on even attempts, fallback on odd. If one
            # provider recovers first (or only one is down), we find it.
            use_fallback = attempt % 2 == 1
            attempt_request = (
                request.override(model=self._fallback_model) if use_fallback else request
            )
            try:
                return await handler(attempt_request)
            except Exception as exc:
                access_error_message = _provider_access_error_message(exc)
                if access_error_message is not None:
                    logger.warning("Model access error surfaced to user: %s", type(exc).__name__)
                    return AIMessage(content=access_error_message)
                if not _should_fallback(exc):
                    raise
                last_exc = exc
                if attempt + 1 >= total_attempts:
                    break
                delay = self._backoff_schedule[attempt]
                if delay > 0:
                    delay += random.uniform(0, delay * 0.25)
                logger.warning(
                    "Model call failed transiently (%s) on %s model "
                    "(attempt %d/%d); retrying %s model in %.1fs",
                    type(exc).__name__,
                    "fallback" if use_fallback else "primary",
                    attempt + 1,
                    total_attempts,
                    "primary" if use_fallback else f"fallback ({self._fallback_name()})",
                    delay,
                )
                if delay > 0:
                    await asyncio.sleep(delay)

        assert last_exc is not None  # loop always sets it before breaking
        logger.error(
            "Model call failed after %d attempts across primary and fallback (%s): %s",
            total_attempts,
            self._fallback_name(),
            last_exc,
        )
        if self._surface_outage_message:
            return AIMessage(content=MODEL_OUTAGE_MESSAGE)
        raise last_exc
