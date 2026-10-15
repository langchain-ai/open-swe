"""Bring the thread index up to date with the Agent Server's threads.

Runs when the job worker starts with ``THREAD_STORE=postgres`` and the index is
not known to be current: on the first switch, and after any time the switch was
off, when only the Agent Server saw new threads and metadata changes.
"""

import logging
from datetime import datetime, timedelta
from typing import Final

from openswe import event_claims
from openswe.remote_runtime.client import MDA_RUNTIME, RUNTIME_METADATA_KEY
from openswe.threads import index
from openswe.threads.index import Runtime, as_status
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
        if not await event_claims.claim(_SCOPE, _CURRENT, ttl=_CURRENT_FOR):
            return 0
        try:
            imported = await _import_all()
        except Exception:
            await event_claims.release(_SCOPE, _CURRENT)
            raise
    finally:
        await event_claims.release(_SCOPE, _RUNNING)
    logger.info("Thread index brought up to date", extra={"thread_count": imported})
    return imported


async def _import_all() -> int:
    client = RUNTIMES.local.client
    imported = 0
    while page := await client.threads.search(
        limit=_PAGE, offset=imported, sort_by="created_at", sort_order="asc"
    ):
        for thread in page:
            metadata = thread.get("metadata") or {}
            created_at = _time(thread.get("created_at"))
            updated_at = _time(thread.get("updated_at"))
            if created_at is None or updated_at is None:
                continue
            runtime: Runtime = (
                "mda" if metadata.get(RUNTIME_METADATA_KEY) == MDA_RUNTIME else "local"
            )
            await index.import_newer(
                thread["thread_id"],
                runtime=runtime,
                metadata=metadata,
                status=as_status(thread.get("status")) or "idle",
                created_at=created_at,
                updated_at=updated_at,
            )
        imported += len(page)
        if len(page) < _PAGE:
            break
    return imported


async def mark_index_stale() -> None:
    """The Agent Server is the thread index again; the next switch has catching up to do."""
    await event_claims.release(_SCOPE, _CURRENT)
