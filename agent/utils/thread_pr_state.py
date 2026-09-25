"""Serialize pull-request lifecycle metadata updates per agent thread."""

import asyncio
import logging
import random
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.errors import ConflictError, NotFoundError

from agent.threads.creation import create_lock_thread
from agent.utils.json_types import thread_metadata

logger = logging.getLogger(__name__)

_LOCK_TTL_MINUTES = 2
_LOCK_TIMEOUT_SECONDS = 60
_LOCK_RETRY_BASE_SECONDS = 0.1
_LOCK_RETRY_MAX_SECONDS = 2.0
_RELEASE_RETRY_DELAYS_SECONDS = (0.5, 1.0, 2.0)


@asynccontextmanager
async def agent_thread_pr_state_lock(
    client: LangGraphClient, thread_id: str
) -> AsyncIterator[None]:
    lock_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"open-swe:pr-state-lock:{thread_id}"))
    owner = str(uuid.uuid4())
    deadline = asyncio.get_running_loop().time() + _LOCK_TIMEOUT_SECONDS
    attempt = 0
    while True:
        try:
            await create_lock_thread(
                client, lock_id, ttl_minutes=_LOCK_TTL_MINUTES, metadata={"lock_owner": owner}
            )
            break
        except ConflictError:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for PR state lock for thread {thread_id}"
                ) from None
            # Jitter keeps waiters on the same lock from retrying in lockstep.
            ceiling = min(_LOCK_RETRY_MAX_SECONDS, _LOCK_RETRY_BASE_SECONDS * 2**attempt)
            await asyncio.sleep(ceiling / 2 + random.uniform(0, ceiling / 2))
            attempt += 1
    try:
        yield
    finally:
        # A failed release holds the lock until its TTL is swept, so ride out brief API outages.
        retrying = False
        for delay in (*_RELEASE_RETRY_DELAYS_SECONDS, None):
            try:
                # A failed DELETE may still have committed and let another waiter acquire.
                if retrying and (
                    thread_metadata(await client.threads.get(lock_id)).get("lock_owner") != owner
                ):
                    break
                await client.threads.delete(lock_id)
                break
            except NotFoundError:
                break
            except Exception:
                retrying = True
                if delay is None:
                    logger.warning(
                        "Failed to release PR state lock",
                        extra={"agent_thread_id": thread_id},
                        exc_info=True,
                    )
                    break
                await asyncio.sleep(delay)
