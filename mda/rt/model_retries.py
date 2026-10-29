"""Model-only retries, bounded waits, and protection for already-streamed output."""

import asyncio
import logging
import math
import random
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from copy import copy
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Final

import httpx
import httpx2
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.callbacks import AsyncCallbackHandler, BaseCallbackHandler
from langchain_core.callbacks.manager import AsyncCallbackManager
from langchain_core.messages import AIMessageChunk
from langchain_core.runnables.config import ensure_config, patch_config, var_child_runnable_config
from langgraph.config import get_stream_writer
from langgraph.errors import GraphBubbleUp
from langgraph.pregel._messages import StreamMessagesHandler  # noqa: PLC2701
from langgraph.pregel.protocol import StreamChunk

from rt.models import middleware_retries
from rt.tracing import MIDDLEWARE_TRACE_POLICY

logger = logging.getLogger(__name__)
MAX_RETRIES: Final = 5
MAX_RETRY_SLEEP_SECONDS: Final = 60.0


class ModelOutput(AsyncCallbackHandler):
    """Track output locally to a model call, never across runs or concurrent calls."""

    def __init__(self) -> None:
        self.streamed = False
        self.deadline: asyncio.Timeout | None = None
        self._messages: dict[str, tuple[StreamMessagesHandler, StreamChunk]] = {}

    def _forward(self, source: StreamMessagesHandler, chunk: StreamChunk) -> None:
        namespace, mode, data = chunk
        if mode == "messages":
            message, _ = data
            if message.id and isinstance(message, AIMessageChunk):
                if message.response_metadata.get("open_swe_superseded"):
                    self._messages.pop(message.id, None)
                else:
                    self._messages[message.id] = (source, chunk)
        source.stream(chunk)

    def supersede(self) -> None:
        """Retract only this failed attempt's visible messages, never graph state."""
        for message_id, (source, chunk) in self._messages.items():
            namespace, mode, (_, metadata) = chunk
            source.stream(
                (
                    namespace,
                    mode,
                    (
                        AIMessageChunk(
                            content="",
                            id=message_id,
                            response_metadata={"open_swe_superseded": True},
                        ),
                        metadata,
                    ),
                )
            )
        self._messages.clear()

    async def on_llm_new_token(self, token: object, **kwargs: object) -> None:
        self.streamed = True
        if self.deadline is not None and not self.deadline.expired():
            self.deadline.reschedule(None)

    async def on_llm_end(self, response: object, **kwargs: object) -> None:
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
        return status in (408, 409, 429) or 500 <= status < 600
    return isinstance(error, TimeoutError | httpx.TransportError | httpx2.TransportError) or (
        type(error).__module__.split(".")[0] in {"openai", "anthropic", "fireworks"}
        and type(error).__name__ in {"APIConnectionError", "APITimeoutError"}
    )


def _retry_after(error: Exception) -> float | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    raw = headers.get("retry-after")
    if not isinstance(raw, str):
        return None
    try:
        seconds = float(raw)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except ValueError:
        try:
            when = parsedate_to_datetime(raw)
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            return max(0.0, (when - datetime.now(UTC)).total_seconds())
        except TypeError, ValueError, OverflowError:
            logger.debug("Ignoring invalid provider Retry-After header")
            return None


class ModelRetriesMiddleware(AgentMiddleware):
    """Retry transient model failures, superseding partial output without replaying tools."""

    trace_policy = MIDDLEWARE_TRACE_POLICY

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        retries = MAX_RETRIES if middleware_retries(request.model) else 0
        spent = 0.0
        for attempt in range(retries + 1):
            output = ModelOutput()
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
                    delay = min(16.0, 2.0**attempt) * random.uniform(0.75, 1.0)
                if delay > MAX_RETRY_SLEEP_SECONDS - spent:
                    logger.warning("Model retry would exceed its sleep budget")
                    raise
                spent += delay
                output.supersede()
                logger.warning(
                    "Retrying model call",
                    extra={"attempt": attempt + 1, "max_retries": retries, "delay_seconds": delay},
                )
                get_stream_writer()(
                    {
                        "type": "model_retry",
                        "attempt": attempt + 1,
                        "max_retries": retries,
                        "delay_seconds": delay,
                        "output_may_have_started": output.streamed,
                    }
                )
                await asyncio.sleep(delay)
        raise RuntimeError("Model retry loop exhausted without a result")
