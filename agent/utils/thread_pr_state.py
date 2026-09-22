"""Serialize pull-request lifecycle metadata updates per agent thread."""

import asyncio
import logging
import random
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from weakref import WeakKeyDictionary, WeakValueDictionary

from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.errors import ConflictError

logger = logging.getLogger(__name__)

_LOCK_TTL_MINUTES = 2
_LOCK_TIMEOUT_SECONDS = 60
_LOCK_RETRY_SECONDS = 0.1
_LOCK_MAX_RETRY_SECONDS = 2
_LOCAL_LOCKS: WeakKeyDictionary[
    asyncio.AbstractEventLoop, WeakValueDictionary[str, asyncio.Lock]
] = WeakKeyDictionary()


@asynccontextmanager
async def agent_thread_pr_state_lock(
    client: LangGraphClient, thread_id: str
) -> AsyncIterator[None]:
    lock_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"open-swe:pr-state-lock:{thread_id}"))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _LOCK_TIMEOUT_SECONDS
    local_locks = _LOCAL_LOCKS.setdefault(loop, WeakValueDictionary())
    local_lock = local_locks.get(thread_id)
    if local_lock is None:
        local_lock = asyncio.Lock()
        local_locks[thread_id] = local_lock
    try:
        async with asyncio.timeout_at(deadline):
            await local_lock.acquire()
    except TimeoutError:
        raise TimeoutError(f"Timed out waiting for PR state lock for thread {thread_id}") from None
    try:
        retry_seconds = _LOCK_RETRY_SECONDS
        while True:
            if loop.time() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for PR state lock for thread {thread_id}"
                ) from None
            try:
                await client.threads.create(
                    thread_id=lock_id, if_exists="raise", ttl=_LOCK_TTL_MINUTES
                )
                break
            except ConflictError:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Timed out waiting for PR state lock for thread {thread_id}"
                    ) from None
                await asyncio.sleep(
                    min(remaining, random.uniform(retry_seconds / 2, retry_seconds))
                )
                retry_seconds = min(retry_seconds * 2, _LOCK_MAX_RETRY_SECONDS)
        try:
            yield
        finally:
            try:
                await client.threads.delete(lock_id)
            except Exception:
                logger.warning(
                    "Failed to release PR state lock",
                    extra={"agent_thread_id": thread_id},
                    exc_info=True,
                )
    finally:
        local_lock.release()
