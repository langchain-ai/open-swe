"""Bounded retry for the sandbox failures the SDK marks as transient."""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from langsmith.sandbox import SandboxRetryableConnectionError

logger = logging.getLogger(__name__)

MAX_TRANSIENT_ATTEMPTS = 4
_BASE_BACKOFF = 0.5
_MAX_BACKOFF = 8.0
_JITTER_FACTOR = 0.2

T = TypeVar("T")


def is_transient_sandbox_error(exc: BaseException) -> bool:
    """Whether the SDK guarantees this failure happened before the command started.

    ``SandboxRetryableConnectionError`` covers a WebSocket upgrade rejected with a
    gateway status (500/502/503/504): the execute frame never went out, so no
    attempt can have run the command, and re-issuing it cannot double-run it.
    """
    return isinstance(exc, SandboxRetryableConnectionError)


def _compute_backoff(attempt: int) -> float:
    base = min(_BASE_BACKOFF * (2 ** (attempt - 1)), _MAX_BACKOFF)
    jitter = base * random.uniform(-_JITTER_FACTOR, _JITTER_FACTOR)
    return min(base + jitter, _MAX_BACKOFF)


async def retry_transient_sandbox_errors(
    operation: Callable[[], Awaitable[T]],
    *,
    description: str,
    max_attempts: int = MAX_TRANSIENT_ATTEMPTS,
) -> T:
    """Run ``operation``, retrying it with backoff while it fails transiently."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return await operation()
        except Exception as exc:
            if attempt >= max_attempts or not is_transient_sandbox_error(exc):
                raise
            delay = _compute_backoff(attempt)
            logger.warning(
                "%s hit a transient sandbox error (%s), retrying in %.1fs (attempt %d/%d)",
                description,
                exc,
                delay,
                attempt,
                max_attempts,
            )
            await asyncio.sleep(delay)
