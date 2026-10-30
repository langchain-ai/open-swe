"""Capability-authenticated callbacks from a thread's sandbox."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response

from openswe.sandboxes.tool_access import (
    TOOLS_HEADER,
    TOOLS_PATH,
    ToolAccess,
    authenticate_tool_access,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix=TOOLS_PATH, tags=["sandbox-tools"])


@router.get("", include_in_schema=False)
@router.get("/list", include_in_schema=False)
@router.get("/search", include_in_schema=False)
@router.post("/invoke/{tool_name}", include_in_schema=False)
async def removed_tool_endpoint() -> None:
    raise HTTPException(
        410,
        "The sandbox tool endpoint was removed. Call the tool directly, "
        "or find it with search_tools and call it from eval.",
    )


async def require_tool_access(request: Request, response: Response) -> ToolAccess:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    token = request.headers.get(TOOLS_HEADER) or request.query_params.get("token")
    return await authenticate_tool_access(token)


Access = Annotated[ToolAccess, Depends(require_tool_access)]


TaskId = Annotated[str, Path(pattern=r"^[A-Za-z0-9-]{1,128}$")]


@router.post("/background-tasks/{task_id}/complete", status_code=204)
async def background_task_complete(task_id: TaskId, access: Access) -> None:
    from openswe.background_tasks import reconcile_background_task_callback

    try:
        result = await reconcile_background_task_callback(task_id, access)
    except PermissionError as exc:
        logger.warning(
            "Background-task completion rejected",
            extra={
                "task_id": task_id,
                "agent_thread_id": access.thread_id,
                "sandbox_id": access.sandbox_id,
            },
        )
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        logger.warning(
            "Background-task completion for an unknown task",
            extra={
                "task_id": task_id,
                "agent_thread_id": access.thread_id,
                "sandbox_id": access.sandbox_id,
            },
        )
        raise HTTPException(404, str(exc)) from exc
    if result.get("pending"):
        # The runner retries on 5xx, which is what redelivers a failed dispatch.
        raise HTTPException(503, "Background task completion is not delivered yet")


@router.post("/background-tasks/{task_id}/heartbeat", status_code=204)
async def background_task_heartbeat(task_id: TaskId, access: Access) -> None:
    from openswe.background_tasks import keep_sandbox_alive

    outcome = await keep_sandbox_alive(access.sandbox_id, task_id)
    if outcome == "unknown":
        raise HTTPException(404, "No such background task")
    if outcome == "finished":
        raise HTTPException(409, "Background task is no longer running")


MonitorId = Annotated[str, Path(pattern=r"^mon-[0-9a-f-]{1,64}$")]


@router.post("/monitors/{monitor_id}/sync", status_code=204)
async def monitor_sync(monitor_id: MonitorId, access: Access) -> None:
    """A monitor's runner has new events or has ended; a 5xx makes a final sync retry."""
    from openswe.monitors.delivery import sync_monitor_callback

    try:
        await sync_monitor_callback(monitor_id, access)
    except PermissionError as exc:
        logger.warning(
            "Monitor sync rejected",
            extra={
                "monitor_id": monitor_id,
                "agent_thread_id": access.thread_id,
                "sandbox_id": access.sandbox_id,
            },
        )
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        logger.warning(
            "Monitor sync for an unknown monitor",
            extra={
                "monitor_id": monitor_id,
                "agent_thread_id": access.thread_id,
                "sandbox_id": access.sandbox_id,
            },
        )
        raise HTTPException(404, str(exc)) from exc
