"""Middleware that retries model calls across a primary and fallback provider.

Wraps the model call. When a model raises a transient provider error (5xx,
429, connection/timeout, or a ``ModelCallTimeoutMiddleware`` deadline), the
request is retried, alternating between the
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

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, cast

import anthropic
import httpx2
import openai
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.exceptions import ModelError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langsmith import trace

from agent.middleware.trace import OpenSWEMiddleware
from agent.utils.errors import classify_exception, error_tracking_fields, exception_fields
from agent.utils.model import evict_cached_model, is_dead_client_error

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
    httpx2.TransportError,
    # Includes ``ModelCallTimeoutMiddleware``'s deadline: a wedged provider call
    # is exactly the case where trying the other provider is worthwhile.
    TimeoutError,
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

_BAD_MODEL_STATE_KEY = "_model_fallback_bad_models"
_BAD_MODEL_TTL_SECONDS = 120.0


def _is_legacy_httpx_transport_error(exc: BaseException) -> bool:
    return exc.__class__.__module__.partition(".")[0] == "httpx" and any(
        cls.__name__ == "TransportError" and cls.__module__.partition(".")[0] == "httpx"
        for cls in exc.__class__.__mro__
    )


def _should_fallback(exc: BaseException) -> bool:
    if isinstance(exc, _TRANSIENT_EXCEPTIONS) or _is_legacy_httpx_transport_error(exc):
        return True
    if isinstance(exc, ModelError):
        return exc.is_retryable
    # Catches OverloadedError (529) and other 5xx/429 surfaced as APIStatusError.
    if isinstance(exc, (anthropic.APIStatusError, openai.APIStatusError)):
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and status in _RETRYABLE_STATUS_CODES:
            return True
    if type(exc) is openai.APIError:
        return classify_exception(exc) == "provider_overloaded"
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


class ModelFallbackMiddleware(OpenSWEMiddleware):
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

    def _bad_model_marks(self, request: ModelRequest) -> dict[int, float] | None:
        raw_state = request.state
        if not isinstance(raw_state, dict):
            return None
        state = cast(dict[str, object], raw_state)
        marks = state.get(_BAD_MODEL_STATE_KEY)
        if not isinstance(marks, dict):
            marks = {}
            state[_BAD_MODEL_STATE_KEY] = marks
        return cast(dict[int, float], marks)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        total_attempts = len(self._backoff_schedule) + 1
        last_exc: BaseException | None = None
        delay = 0.0
        bad_model_marks = self._bad_model_marks(request)
        now = time.monotonic()
        primary_key = id(request.model)
        initial_fallback = False
        if bad_model_marks is not None:
            expired = [key for key, expires_at in bad_model_marks.items() if expires_at <= now]
            for key in expired:
                del bad_model_marks[key]
            initial_fallback = primary_key in bad_model_marks

        for attempt in range(total_attempts):
            # Alternate: primary on even attempts, fallback on odd. If one
            # provider recovers first (or only one is down), we find it.
            use_fallback = (attempt + (1 if initial_fallback else 0)) % 2 == 1
            attempt_request = (
                request.override(model=self._fallback_model) if use_fallback else request
            )
            try:
                if last_exc is None:
                    response = await handler(attempt_request)
                    if not use_fallback and bad_model_marks is not None:
                        bad_model_marks.pop(primary_key, None)
                    return response
                failed_model = request.model if use_fallback else self._fallback_model
                metadata: dict[str, str | int | float | None] = {
                    "attempt": attempt + 1,
                    "max_attempts": total_attempts,
                    "failed_model": str(
                        getattr(failed_model, "model_name", None)
                        or getattr(failed_model, "model", "unknown")
                    ),
                    "next_model": str(
                        getattr(attempt_request.model, "model_name", None)
                        or getattr(attempt_request.model, "model", "unknown")
                    ),
                    "error_type": type(last_exc).__name__,
                    "backoff_seconds": delay,
                    "retry_with": "fallback" if use_fallback else "primary",
                }
                status_code = getattr(last_exc, "status_code", None)
                if isinstance(status_code, int):
                    metadata["status_code"] = status_code
                retry_error: Exception | None = None
                async with trace("model_retry", inputs={}, metadata=metadata) as retry_span:
                    try:
                        if delay > 0:
                            await asyncio.sleep(delay)
                        response = await handler(attempt_request)
                    except Exception as exc:
                        retry_span.end(error=type(exc).__name__)
                        retry_error = exc
                    else:
                        retry_span.end(outputs={"outcome": "success"})
                        if not use_fallback and bad_model_marks is not None:
                            bad_model_marks.pop(primary_key, None)
                        return response
                if retry_error is not None:
                    raise retry_error
            except Exception as exc:
                fields = exception_fields(exc)
                access_error_message = _provider_access_error_message(exc)
                if access_error_message is not None:
                    logger.warning(
                        "Model access error surfaced to user",
                        extra={**error_tracking_fields(exc), "model_access_error": fields},
                    )
                    return AIMessage(content=access_error_message)
                if not _should_fallback(exc):
                    raise
                dead_client_failure = not use_fallback and is_dead_client_error(exc)
                if dead_client_failure:
                    if bad_model_marks is not None:
                        bad_model_marks[primary_key] = time.monotonic() + _BAD_MODEL_TTL_SECONDS
                    await evict_cached_model(attempt_request.model)
                last_exc = exc
                if attempt + 1 >= total_attempts:
                    break
                delay = 0.0 if dead_client_failure else self._backoff_schedule[attempt]
                if delay > 0 and not dead_client_failure:
                    delay += random.uniform(0, delay * 0.25)
                failed_on = "fallback" if use_fallback else "primary"
                retry_with = "primary" if use_fallback else "fallback"
                logger.warning(
                    "Model call failed transiently; retrying",
                    extra={
                        **error_tracking_fields(exc),
                        "model_call_retry": {
                            "failed_on": failed_on,
                            "attempt": attempt + 1,
                            "attempts": total_attempts,
                            "retry_with": retry_with,
                            "fallback_model": self._fallback_name(),
                            "delay_seconds": delay,
                            **fields,
                        },
                    },
                )

        assert last_exc is not None  # loop always sets it before breaking
        last_fields = exception_fields(last_exc)
        logger.error(
            "Model call failed after retries across primary and fallback",
            exc_info=last_exc,
            extra={
                **error_tracking_fields(last_exc),
                "model_call_failure": {
                    "attempts": total_attempts,
                    "fallback_model": self._fallback_name(),
                    **last_fields,
                },
            },
        )
        if self._surface_outage_message:
            return AIMessage(content=MODEL_OUTAGE_MESSAGE)
        raise last_exc
