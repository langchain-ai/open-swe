"""What a machine caller may read back about the threads it started.

A key and a workflow are not people: they have no viewed state, no pinned list
and no participants, and the dashboard's per-user reads are all built around
those. So they get one flat view of a thread and one flat list, scoped by the
caller id stamped on the threads they started.
"""

import logging
from typing import Any

from fastapi import HTTPException

from agent.threads.callers import STARTED_BY_ID, Caller
from agent.threads.summary import run_status_to_agent_status
from agent.utils.dashboard_links import dashboard_thread_url
from agent.utils.json_types import JsonObject, thread_metadata
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

MAX_MACHINE_PAGE = 100


def _view(thread: Any, thread_id: str, metadata: JsonObject) -> JsonObject:
    status = thread.get("status") if isinstance(thread, dict) else None
    stored = metadata.get("latest_run_status")
    title = metadata.get("title")
    return {
        "thread_id": thread_id,
        "status": run_status_to_agent_status(
            status if isinstance(status, str) else None,
            stored if isinstance(stored, str) else None,
        ),
        "title": title if isinstance(title, str) else None,
        "workspace": metadata.get("workspace"),
        "url": dashboard_thread_url(thread_id),
    }


async def machine_thread(thread_id: str, caller: Caller) -> JsonObject:
    """One thread this caller started, or 404."""
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Thread lookup failed for %s", thread_id, exc_info=True)
        raise HTTPException(404, "thread not found") from exc
    metadata = thread_metadata(thread)
    caller.assert_can_read(metadata)
    return _view(thread, thread_id, metadata)


async def machine_threads(caller: Caller, *, limit: int = 25) -> list[JsonObject]:
    """The threads this caller started, newest first."""
    threads = await langgraph_client().threads.search(
        metadata={STARTED_BY_ID: caller.started_by_id},
        limit=min(max(limit, 1), MAX_MACHINE_PAGE),
    )
    views: list[JsonObject] = []
    for thread in threads:
        metadata = thread_metadata(thread)
        thread_id = thread.get("thread_id") if isinstance(thread, dict) else None
        if isinstance(thread_id, str):
            views.append(_view(thread, thread_id, metadata))
    return views
