"""Bounded model calls: retries, deadlines, and a fallback within one budget.

A provider stall that never raises would park a run with nothing for a retry or
a fallback to react to, and retries stacked in the provider SDK and in middleware
multiply each other. Here middleware owns the retries for providers whose SDK
retries :func:`disable_sdk_retries` turned off, and :class:`ModelCallBudget`
bounds a call, its retries and its fallback by one deadline.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, MutableMapping
from contextlib import asynccontextmanager, contextmanager
from copy import copy
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Final, TypeVar

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.callbacks import AsyncCallbackHandler, BaseCallbackHandler
from langchain_core.callbacks.manager import AsyncCallbackManager
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessageChunk
from langchain_core.runnables.config import ensure_config, patch_config, var_child_runnable_config
from langgraph.config import get_stream_writer
from langgraph.errors import GraphBubbleUp
from langgraph.pregel._messages import StreamMessagesHandler
from langgraph.pregel.protocol import StreamChunk

try:
    from langchain.agents.middleware import TracePolicy, omit_payload
except ImportError:  # langchain before 1.4.7
    _TRACE_POLICY = None
else:
    # A model call's own trace already has the conversation its request carries.
    _TRACE_POLICY = TracePolicy(process_inputs=omit_payload)

__all__ = [
    "MAX_RETRIES",
    "MAX_RETRY_SLEEP_SECONDS",
    "SUPERSEDED",
    "ModelCallBudget",
    "ModelCallTimeoutError",
    "ModelCallTimeoutMiddleware",
    "ModelRetriesMiddleware",
    "disable_sdk_retries",
]

logger = logging.getLogger(__name__)

_V = TypeVar("_V")

MAX_RETRIES: Final = 5
MAX_RETRY_SLEEP_SECONDS: Final = 60.0
#: ``response_metadata`` key of the empty chunk that retracts a failed attempt's
#: streamed message, so a client can drop what it showed of it.
SUPERSEDED: Final = "mda_superseded"
# init_chat_model's provider -> its integration package, and the ``max_retries``
# that leaves retrying to middleware. Google counts total attempts.
_SDK_RETRIES: Final = {
    "openai": ("langchain_openai", 0),
    "anthropic": ("langchain_anthropic", 0),
    "fireworks": ("langchain_fireworks", 0),
    "google_genai": ("langchain_google_genai", 1),
}
_RETRIES_OFF: Final = dict(_SDK_RETRIES.values())
_TRANSIENT_STATUS: Final = frozenset({408, 409, 429})
_TRANSIENT_ERRORS: Final = frozenset(
    {
        ("httpx", "TransportError"),
        ("httpx2", "TransportError"),
        ("openai", "APIConnectionError"),
        ("anthropic", "APIConnectionError"),
        ("fireworks", "APIConnectionError"),
    }
)
# Bindings nest a few deep at most; the bound stops a self-referencing chain.
_MAX_BINDINGS: Final = 10


class ModelCallTimeoutError(TimeoutError):
    """A model call exceeded its wall-clock deadline."""


@asynccontextmanager
async def _deadline(seconds: float) -> AsyncIterator[asyncio.Timeout]:
    """Bound model attempts together without reclassifying provider timeouts."""
    timeout = asyncio.timeout(seconds)
    try:
        async with timeout:
            yield timeout
    except TimeoutError as exc:
        if not timeout.expired():
            raise
        logger.warning("Model call exceeded its deadline", extra={"timeout_seconds": seconds})
        raise ModelCallTimeoutError(f"Model call exceeded the {seconds}s deadline") from exc


def disable_sdk_retries(provider: str, kwargs: MutableMapping[str, _V | int]) -> None:
    """Turn off ``provider``'s SDK retries in its chat model ``kwargs``.

    :class:`ModelRetriesMiddleware` then owns them; a provider not known here keeps
    its SDK's retries and gets none from the middleware.
    """
    if provider not in _SDK_RETRIES:
        return
    _, off = _SDK_RETRIES[provider]
    if kwargs.get("max_retries", off) != off:
        logger.warning(
            "Ignoring SDK retry override because model middleware owns retries",
            extra={"provider": provider},
        )
    kwargs["max_retries"] = off


def _retries_owned(model: object) -> bool:
    """Whether ``model``'s SDK retries are known and turned off."""
    for _ in range(_MAX_BINDINGS):
        bound = getattr(model, "bound", None)
        if bound is None or bound is model:
            break
        model = bound
    for cls in type(model).__mro__:
        package = cls.__module__.split(".")[0]
        if package in _RETRIES_OFF:
            return getattr(model, "max_retries", None) == _RETRIES_OFF[package]
    return False


class _ModelOutput(AsyncCallbackHandler):
    """What one model call streamed, never across runs or concurrent calls."""

    def __init__(self) -> None:
        self.streamed = False
        #: Lifted once output starts, so a stream that started is not cut off.
        self.first_output: asyncio.Timeout | None = None
        self._messages: dict[str, tuple[StreamMessagesHandler, StreamChunk]] = {}

    def _forward(self, source: StreamMessagesHandler, chunk: StreamChunk) -> None:
        _, mode, data = chunk
        if mode == "messages":
            message, _ = data
            if message.id and isinstance(message, AIMessageChunk):
                if message.response_metadata.get(SUPERSEDED):
                    self._messages.pop(message.id, None)
                else:
                    self._messages[message.id] = (source, chunk)
        source.stream(chunk)

    def supersede(self) -> None:
        """Retract this failed attempt's streamed messages, never graph state."""
        for message_id, (source, chunk) in self._messages.items():
            namespace, mode, (_, metadata) = chunk
            retraction = AIMessageChunk(
                content="", id=message_id, response_metadata={SUPERSEDED: True}
            )
            source.stream((namespace, mode, (retraction, metadata)))
        self._messages.clear()

    async def on_llm_new_token(self, token: object, **kwargs: object) -> None:  # noqa: ARG002
        self.streamed = True
        if self.first_output is not None and not self.first_output.expired():
            self.first_output.reschedule(None)

    async def on_llm_end(self, response: object, **kwargs: object) -> None:  # noqa: ARG002
        self.streamed = True

    @contextmanager
    def tracking(self) -> Iterator[None]:
        config = ensure_config()
        callbacks = AsyncCallbackManager.configure(config.get("callbacks"))
        replacements: dict[int, StreamMessagesHandler] = {}

        def tracked(handler: BaseCallbackHandler) -> BaseCallbackHandler:
            if not isinstance(handler, StreamMessagesHandler):
                return handler
            if id(handler) not in replacements:
                replacement = copy(handler)
                replacement.stream = lambda chunk, source=handler: self._forward(source, chunk)
                replacements[id(handler)] = replacement
            return replacements[id(handler)]

        callbacks.handlers = [tracked(handler) for handler in callbacks.handlers]
        callbacks.inheritable_handlers = [
            tracked(handler) for handler in callbacks.inheritable_handlers
        ]
        callbacks.add_handler(self, inherit=True)
        token = var_child_runnable_config.set(patch_config(config, callbacks=callbacks))
        try:
            yield
        finally:
            var_child_runnable_config.reset(token)


def _status(error: Exception) -> int | None:
    code = getattr(error, "status_code", None)
    if not isinstance(code, int):
        code = getattr(error, "code", None)
    return code if isinstance(code, int) else None


def _transient(error: Exception) -> bool:
    if isinstance(error, GraphBubbleUp):
        return False
    if (status := _status(error)) is not None:
        return status in _TRANSIENT_STATUS or 500 <= status < 600  # noqa: PLR2004
    return isinstance(error, TimeoutError) or any(
        (cls.__module__.split(".")[0], cls.__name__) in _TRANSIENT_ERRORS
        for cls in type(error).__mro__
    )


def _retry_after(error: Exception) -> float | None:
    headers = getattr(getattr(error, "response", None), "headers", None)
    if headers is None:
        return None
    raw = headers.get("retry-after")
    if not isinstance(raw, str):
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            logger.debug("Ignoring invalid provider Retry-After header")
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return max(0.0, (when - datetime.now(UTC)).total_seconds())
    return max(0.0, seconds) if math.isfinite(seconds) else None


class ModelCallTimeoutMiddleware(AgentMiddleware):
    """Fail a model call that outlives its deadline instead of letting it hang."""

    trace_policy = _TRACE_POLICY

    def __init__(self, timeout_seconds: float) -> None:
        super().__init__()
        self._timeout_seconds = timeout_seconds

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        async with _deadline(self._timeout_seconds):
            return await handler(request)


class ModelRetriesMiddleware(AgentMiddleware):
    """Retry transient model failures, retracting partial output, never replaying tools.

    Only a model whose SDK retries :func:`disable_sdk_retries` turned off is
    retried. Backoff is jittered and honors ``Retry-After``, within
    ``max_retry_sleep_seconds`` of sleep per call; a hint beyond that fails the
    call rather than retrying before the provider permits it. Each retry is
    logged and streamed as a ``model_retry`` custom event, without the error text.
    """

    trace_policy = _TRACE_POLICY

    def __init__(
        self,
        *,
        max_retries: int = MAX_RETRIES,
        max_retry_sleep_seconds: float = MAX_RETRY_SLEEP_SECONDS,
    ) -> None:
        super().__init__()
        self._max_retries = max_retries
        self._max_retry_sleep_seconds = max_retry_sleep_seconds

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        retries = self._max_retries if _retries_owned(request.model) else 0
        spent = 0.0
        for attempt in range(retries + 1):
            output = _ModelOutput()
            try:
                with output.tracking():
                    return await handler(request)
            except GraphBubbleUp:
                raise
            except Exception as exc:
                if attempt == retries or not _transient(exc):
                    raise
                delay = _retry_after(exc)
                if delay is None:
                    delay = min(16.0, 2.0**attempt) * random.uniform(0.75, 1.0)  # noqa: S311
                if delay > self._max_retry_sleep_seconds - spent:
                    logger.warning("Model retry would exceed its sleep budget")
                    raise
                spent += delay
                output.supersede()
                retry = {"attempt": attempt + 1, "max_retries": retries, "delay_seconds": delay}
                logger.warning("Retrying model call", extra=retry)
                get_stream_writer()(
                    {"type": "model_retry", **retry, "output_may_have_started": output.streamed}
                )
                await asyncio.sleep(delay)
        raise RuntimeError("Model retry loop exhausted without a result")


def _always(_: Exception) -> bool:
    return True


class ModelCallBudget:
    """One elapsed budget for a model call, its retries, and its fallback.

    With a fallback, the whole primary call, retries and their waits included, has
    ``first_output_seconds`` (two thirds of the budget by default) to start
    streaming; a silent stall then leaves the rest to the fallback. Once the
    primary streams, that window is lifted for the rest of its call, later retries
    included. Without a fallback the primary has the whole budget. Expiry raises
    :class:`ModelCallTimeoutError`.
    """

    def __init__(
        self, timeout_seconds: float, *, first_output_seconds: float | None = None
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._first_output_seconds = (
            timeout_seconds * 2 / 3 if first_output_seconds is None else first_output_seconds
        )

    async def call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
        *,
        fallback: Callable[[], BaseChatModel] | None = None,
        falls_back: Callable[[Exception], bool] = _always,
    ) -> ModelResponse:
        """``request``, then, if it fails as ``falls_back`` accepts, the model ``fallback`` builds.

        ``fallback`` runs only once needed, after what the primary streamed is retracted.
        """
        async with _deadline(self._timeout_seconds):
            output = _ModelOutput()
            try:
                with output.tracking():
                    if fallback is None:
                        return await handler(request)
                    async with _deadline(self._first_output_seconds) as first_output:
                        output.first_output = first_output
                        return await handler(request)
            except Exception as exc:
                if fallback is None or isinstance(exc, GraphBubbleUp) or not falls_back(exc):
                    raise
                logger.warning("Model call failed; retrying on the fallback model", exc_info=True)
                output.supersede()
                return await handler(request.override(model=fallback()))
