"""Serialize per-agent-thread operations across server processes.

Uses LangGraph's own thread-creation atomicity (``if_exists="raise"``) as a
distributed mutex, keyed by thread id and lock scope — safe across any number
of server processes/replicas, unlike an in-process lock.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from langgraph_sdk.errors import ConflictError

logger = logging.getLogger(__name__)

_LOCK_TTL_MINUTES = 2
_LOCK_TIMEOUT_SECONDS = 60
_LOCK_RETRY_SECONDS = 0.1


@asynccontextmanager
async def _thread_scoped_lock(client: Any, thread_id: str, scope: str) -> AsyncIterator[None]:
    lock_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"open-swe:{scope}:{thread_id}"))
    deadline = asyncio.get_running_loop().time() + _LOCK_TIMEOUT_SECONDS
    while True:
        try:
            await client.threads.create(thread_id=lock_id, if_exists="raise", ttl=_LOCK_TTL_MINUTES)
            break
        except ConflictError:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for {scope} lock for thread {thread_id}"
                ) from None
            await asyncio.sleep(_LOCK_RETRY_SECONDS)
    try:
        yield
    finally:
        try:
            await client.threads.delete(lock_id)
        except Exception:
            logger.warning(
                "Failed to release %s lock for thread %s", scope, thread_id, exc_info=True
            )


def agent_thread_pr_state_lock(client: Any, thread_id: str) -> AbstractAsyncContextManager[None]:
    return _thread_scoped_lock(client, thread_id, "pr-state-lock")


def agent_thread_enqueue_lock(client: Any, thread_id: str) -> AbstractAsyncContextManager[None]:
    """Serialize ``POST /threads/{id}/runs`` enqueue dispatches for one thread.

    Without this, two enqueue calls racing on the same busy thread each
    compute their dedup snapshot (``persisted_message_ids`` / dynamic-context
    hashes) from the same pre-dispatch thread state, since neither of their
    messages has been persisted yet — a client_message_id collision or
    duplicate dynamic-context injection between the two queued runs would go
    uncaught. Serializing the enrich-then-dispatch sequence closes that.
    """
    return _thread_scoped_lock(client, thread_id, "enqueue-lock")
