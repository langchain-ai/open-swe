"""The task a thread coordinates, for a client that serves its sandbox until it settles."""

import logging

from fastapi import APIRouter, HTTPException

from openswe.audit_logs.middleware import audit_endpoint
from openswe.tasks.activity import CoordinatorActivity
from openswe.threads.principals import PrincipalDep
from openswe.utils.json_types import JsonObject, thread_metadata
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

router = APIRouter()


async def _metadata(thread_id: str) -> JsonObject:
    try:
        return thread_metadata(await langgraph_client().threads.get(thread_id))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Thread lookup failed", extra={"agent_thread_id": thread_id}, exc_info=True)
        raise HTTPException(404, "thread not found") from exc


@router.get("/threads/{thread_id}/workers")
async def api_thread_workers(
    thread_id: str, principal: PrincipalDep, after: str | None = None
) -> CoordinatorActivity:
    """The thread's workers, and its runs created after the run ``after``."""
    principal.assert_can_read(await _metadata(thread_id))
    return await CoordinatorActivity.of(thread_id, after=after)


@router.post("/threads/{thread_id}/workers/cancel")
@audit_endpoint
async def api_cancel_thread_workers(thread_id: str, principal: PrincipalDep) -> CoordinatorActivity:
    """Stop every worker of the thread's task that is still running, without it reporting back."""
    principal.assert_can_post(await _metadata(thread_id))
    await (await CoordinatorActivity.of(thread_id)).stop_workers()
    return await CoordinatorActivity.of(thread_id)
