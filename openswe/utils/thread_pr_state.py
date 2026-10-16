"""Serialize pull-request lifecycle metadata updates per agent thread."""

import asyncio
import logging
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Final

from openswe import event_claims

logger = logging.getLogger(__name__)

_SCOPE: Final = "pr_state_lock"
_LOCK_TTL = timedelta(minutes=2)
_LOCK_TIMEOUT_SECONDS = 60
_LOCK_RETRY_BASE_SECONDS = 0.1
_LOCK_RETRY_MAX_SECONDS = 2.0
_RELEASE_RETRY_DELAYS_SECONDS = (0.5, 1.0, 2.0)


@asynccontextmanager
async def agent_thread_pr_state_lock(thread_id: str) -> AsyncIterator[None]:
    deadline = asyncio.get_running_loop().time() + _LOCK_TIMEOUT_SECONDS
    attempt = 0
    while not await event_claims.claim(_SCOPE, thread_id, ttl=_LOCK_TTL):
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(f"Timed out waiting for PR state lock for thread {thread_id}")
        # Jitter keeps waiters on the same lock from retrying in lockstep.
        ceiling = min(_LOCK_RETRY_MAX_SECONDS, _LOCK_RETRY_BASE_SECONDS * 2**attempt)
        await asyncio.sleep(ceiling / 2 + random.uniform(0, ceiling / 2))
        attempt += 1
    try:
        yield
    finally:
        # A failed release holds the lock until it lapses, so ride out brief outages.
        for delay in (*_RELEASE_RETRY_DELAYS_SECONDS, None):
            try:
                await event_claims.release(_SCOPE, thread_id)
                break
            except Exception:
                if delay is None:
                    logger.warning(
                        "Failed to release PR state lock",
                        extra={"agent_thread_id": thread_id},
                        exc_info=True,
                    )
                    break
                await asyncio.sleep(delay)
