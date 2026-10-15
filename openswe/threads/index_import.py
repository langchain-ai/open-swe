"""Bring the thread index up to date with the Agent Server's threads.

Runs when the job worker starts with ``THREAD_STORE=postgres`` and the index is
not known to be current: on the first switch, and after any time the switch was
off, when only the Agent Server saw new threads and metadata changes.
"""

import logging
from datetime import datetime, timedelta
from typing import Final

from langgraph_sdk.schema import Thread

from openswe import event_claims
from openswe.remote_runtime.client import MDA_RUNTIME, RUNTIME_METADATA_KEY
from openswe.threads import index
from openswe.threads.index import IndexedThread, as_status
from openswe.threads.runtimes import RUNTIMES

logger = logging.getLogger(__name__)

_PAGE: Final = 200
_SCOPE: Final = "threads"
_RUNNING: Final = "index-import"
# Held while the index is current; released whenever the backend starts without it.
_CURRENT: Final = "index-current"
_CURRENT_FOR: Final = timedelta(days=3650)


def _time(value: object) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) and value else None


async def import_agent_server_threads() -> int:
    """Index every Agent Server thread the index lacks or has an older copy of."""
    if not await event_claims.claim(_SCOPE, _RUNNING, ttl=timedelta(minutes=30)):
        return 0
    try:
        if await event_claims.held(_SCOPE, _CURRENT):
            return 0
        # Current only once every page is in: a killed import runs again on the next start.
        imported = await _import_all()
        await event_claims.claim(_SCOPE, _CURRENT, ttl=_CURRENT_FOR)
    finally:
        await event_claims.release(_SCOPE, _RUNNING)
    logger.info("Thread index brought up to date", extra={"thread_count": imported})
    return imported


async def _import_all() -> int:
    client = RUNTIMES.local.client
    imported = 0
    # Only what the index keeps: a thread's values are its whole conversation.
    while page := await client.threads.search(
        limit=_PAGE,
        offset=imported,
        sort_by="created_at",
        sort_order="asc",
        select=["thread_id", "metadata", "status", "created_at", "updated_at"],
    ):
        await index.import_newer([thread for raw in page if (thread := _indexed(raw))])
        imported += len(page)
        if len(page) < _PAGE:
            break
    return imported


def _indexed(thread: Thread) -> IndexedThread | None:
    metadata = thread.get("metadata") or {}
    created_at = _time(thread.get("created_at"))
    updated_at = _time(thread.get("updated_at"))
    if created_at is None or updated_at is None:
        return None
    return IndexedThread(
        thread_id=thread["thread_id"],
        runtime="mda" if metadata.get(RUNTIME_METADATA_KEY) == MDA_RUNTIME else "local",
        metadata=metadata,
        status=as_status(thread.get("status")) or "idle",
        created_at=created_at,
        updated_at=updated_at,
    )


async def mark_index_stale() -> None:
    """The Agent Server is the thread index again; the next switch has catching up to do."""
    await event_claims.release(_SCOPE, _CURRENT)
