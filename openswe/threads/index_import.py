"""Bring the thread index up to date with the Agent Server's threads.

Runs when the job worker starts and the index is not known to be current: the
first start of a deployment whose Agent Server already held threads. From then
on the index stays current through the writes that keep it.
"""

import logging
from datetime import timedelta
from typing import Final

from openswe import event_claims
from openswe.threads import index
from openswe.threads.index import INDEXED_FIELDS, IndexedThread
from openswe.threads.runtimes import RUNTIMES

logger = logging.getLogger(__name__)

_PAGE: Final = 200
_SCOPE: Final = "threads"
_RUNNING: Final = "index-import"
# Held once the index is current.
_CURRENT: Final = "index-current"
_CURRENT_FOR: Final = timedelta(days=3650)


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
    while page := await client.threads.search(
        limit=_PAGE,
        offset=imported,
        sort_by="created_at",
        sort_order="asc",
        select=INDEXED_FIELDS,
    ):
        await index.import_newer(
            [thread for raw in page if (thread := IndexedThread.from_agent_server(raw))]
        )
        imported += len(page)
        if len(page) < _PAGE:
            break
    return imported
