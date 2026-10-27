"""One model turn's provider requests, deadline and fallback selection."""

import asyncio
import logging
import math
import random
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import anthropic
import httpx
import httpx2
import openai
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables.config import ensure_config, merge_configs, set_config_context

logger = logging.getLogger(__name__)

_fallback: ContextVar[Callable[[], BaseChatModel] | None] = ContextVar(
    "model_fallback", default=None
)


@contextmanager
def model_fallback(model: Callable[[], BaseChatModel] | None) -> Iterator[None]:
    token = _fallback.set(model)
    try:
        yield
    finally:
        _fallback.reset(token)


def fallback_model() -> BaseChatModel | None:
    selected = _fallback.get()
    return selected() if selected is not None else None


def transient_model_error(error: Exception) -> bool:
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    if isinstance(status, int):
        return status in {408, 409, 429} or 500 <= status < 600
    return isinstance(
        error,
        TimeoutError
        | ConnectionError
        | httpx.TransportError
        | httpx2.TransportError
        | openai.APIConnectionError
        | anthropic.APIConnectionError,
    )


def retry_delay(error: Exception, attempt: int) -> float:
    response = getattr(error, "response", None)
    value = getattr(response, "headers", {}).get("retry-after")
    if isinstance(value, str):
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
            except ValueError, TypeError, OverflowError:
                logger.warning("Provider sent an invalid Retry-After", extra={"retry_after": value})
            else:
                return max(0.0, seconds)
        else:
            if math.isfinite(seconds):
                return max(0.0, seconds)
    return 2**attempt * random.uniform(0.75, 1.25)


class ModelCallBudget(AsyncCallbackHandler):
    """Count summary and generation requests, and forbid replay after streamed output."""

    def __init__(self, max_attempts: int = 6) -> None:
        self.remaining = max_attempts
        self.emitted = False

    async def on_llm_new_token(self, token: object, **kwargs: object) -> None:
        self.emitted = True

    def can_retry(self, error: Exception) -> bool:
        return self.remaining > 0 and not self.emitted and transient_model_error(error)

    async def call[Result](
        self, invoke: Callable[[], Awaitable[Result]], *, streaming: bool = True
    ) -> Result:
        for attempt in range(2):
            if self.remaining <= 0:
                raise RuntimeError("Model call exhausted its provider request budget")
            self.remaining -= 1
            try:
                if not streaming:
                    return await invoke()
                config = merge_configs(ensure_config(), {"callbacks": [self]})

                async def run() -> Result:
                    return await invoke()

                with set_config_context(config) as context:
                    return await asyncio.create_task(run(), context=context)
            except Exception as exc:
                if attempt == 1 or not self.can_retry(exc):
                    raise
                delay = retry_delay(exc, attempt)
                logger.warning(
                    "Retrying a transient model failure",
                    extra={"delay_seconds": delay, "attempts_remaining": self.remaining},
                    exc_info=True,
                )
                await asyncio.sleep(delay)
        raise AssertionError("No model request made")
