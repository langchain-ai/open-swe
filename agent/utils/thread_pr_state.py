"""Serialize pull-request lifecycle metadata updates per agent thread."""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph_sdk.errors import ConflictError

logger = logging.getLogger(__name__)

_LOCK_TTL_MINUTES = 2
_LOCK_TIMEOUT_SECONDS = 60
_LOCK_RETRY_SECONDS = 0.1


@asynccontextmanager
async def agent_thread_pr_state_lock(client: Any, thread_id: str) -> AsyncIterator[None]:
    lock_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"open-swe:pr-state-lock:{thread_id}"))
    deadline = asyncio.get_running_loop().time() + _LOCK_TIMEOUT_SECONDS
    while True:
        try:
            await client.threads.create(thread_id=lock_id, if_exists="raise", ttl=_LOCK_TTL_MINUTES)
            break
        except ConflictError:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for PR state lock for thread {thread_id}"
                ) from None
            await asyncio.sleep(_LOCK_RETRY_SECONDS)
    try:
        yield
    finally:
        try:
            await client.threads.delete(lock_id)
        except Exception:
            logger.warning(
                "Failed to release PR state lock for thread %s", thread_id, exc_info=True
            )
