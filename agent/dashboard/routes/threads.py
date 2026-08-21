"""Agent threads: listing, reading, messaging, cancelling, and the SDK proxies.

Every endpoint here is a thin delegate into :mod:`agent.dashboard.threads`,
which owns the authorization checks and the LangGraph round-trips.
"""

import logging
from time import perf_counter
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ...utils.timing import server_timing_header
from ..authz import SESSION, session_is_admin
from ..threads.listing import (
    get_dashboard_thread,
    list_dashboard_threads,
    list_dashboard_threads_page,
    list_dashboard_threads_sidebar,
)
from ..threads.runs import (
    ThreadMessageBody,
    ThreadResolveBody,
    cancel_dashboard_thread,
    delete_dashboard_thread,
    get_dashboard_thread_state,
    proxy_dashboard_thread_commands,
    proxy_dashboard_thread_history,
    proxy_dashboard_thread_run_cancel,
    proxy_dashboard_thread_stream_events,
    resolve_dashboard_thread,
    send_dashboard_message,
    stream_dashboard_thread,
)
from ..threads.sandbox import (
    get_dashboard_thread_pr_diff,
    get_dashboard_thread_recovery_patch,
    get_dashboard_thread_turn_diff,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_admin_for_all(session: dict[str, Any], include_all: bool) -> None:
    if include_all and not session_is_admin(session):
        raise HTTPException(403, "admin only")


@router.get("/threads")
async def api_list_threads(
    all: bool = False,
    session: dict[str, Any] = SESSION,
) -> list[dict[str, Any]]:
    _require_admin_for_all(session, all)
    return await list_dashboard_threads(session["sub"], email=session.get("email"), include_all=all)


@router.get("/threads/sidebar")
async def api_list_threads_sidebar(
    active_limit: int = 50,
    resolved_limit: int = 20,
    active_thread_id: str | None = None,
    include_automations: bool = False,
    all: bool = False,
    session: dict[str, Any] = SESSION,
) -> Response:
    _require_admin_for_all(session, all)
    timings: dict[str, float] = {}
    counts: dict[str, int] = {}
    started = perf_counter()
    payload = await list_dashboard_threads_sidebar(
        session["sub"],
        email=session.get("email"),
        active_limit=active_limit,
        resolved_limit=resolved_limit,
        active_thread_id=active_thread_id,
        include_automations=include_automations,
        include_all=all,
        timings=timings,
        counts=counts,
    )
    timings["total"] = (perf_counter() - started) * 1000
    header = server_timing_header(timings, counts)
    logger.info("thread sidebar timings login=%s %s", session["sub"], header)
    return JSONResponse(payload, headers={"Server-Timing": header})


@router.get("/threads/page")
async def api_list_threads_page(
    limit: int = 25,
    offset: int = 0,
    all: bool = False,
    resolved: bool | None = None,
    viewed: bool | None = None,
    source: str | None = None,
    status: str | None = None,
    q: str | None = None,
    scope: Literal["all", "interactive", "automation"] = "all",
    automation_id: str | None = None,
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    _require_admin_for_all(session, all)
    return await list_dashboard_threads_page(
        session["sub"],
        email=session.get("email"),
        limit=limit,
        offset=offset,
        include_all=all,
        resolved=resolved,
        viewed=viewed,
        source=source,
        status=status,
        query=q,
        scope=scope,
        automation_id=automation_id,
    )


@router.get("/threads/{thread_id}")
async def api_get_thread(
    thread_id: str,
    mark_viewed: bool = True,
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    return await get_dashboard_thread(
        thread_id,
        session["sub"],
        email=session.get("email"),
        mark_viewed=mark_viewed,
    )


@router.get("/threads/{thread_id}/recovery.patch")
async def api_get_thread_recovery_patch(
    thread_id: str,
    session: dict[str, Any] = SESSION,
) -> Response:
    content, filename = await get_dashboard_thread_recovery_patch(
        thread_id,
        session["sub"],
        email=session.get("email"),
    )
    return Response(
        content=content,
        media_type="text/x-diff",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/threads/{thread_id}/turn-diff")
async def api_get_thread_turn_diff(
    thread_id: str,
    turn_key: str | None = None,
    max_files: int = Query(200, ge=1, le=200),
    include_content: bool = True,
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    return await get_dashboard_thread_turn_diff(
        thread_id,
        session["sub"],
        turn_key=turn_key,
        max_files=max_files,
        include_content=include_content,
        email=session.get("email"),
    )


@router.get("/threads/{thread_id}/pr-diff")
async def api_get_thread_pr_diff(
    thread_id: str,
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    return await get_dashboard_thread_pr_diff(
        thread_id,
        session["sub"],
        email=session.get("email"),
    )


@router.post("/threads/{thread_id}/messages")
async def api_send_thread_message(
    thread_id: str,
    body: ThreadMessageBody,
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    return await send_dashboard_message(thread_id, session["sub"], body, email=session.get("email"))


@router.post("/threads/{thread_id}/resolve")
async def api_resolve_thread(
    thread_id: str,
    body: ThreadResolveBody,
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    return await resolve_dashboard_thread(
        thread_id,
        session["sub"],
        resolved=body.resolved,
        email=session.get("email"),
    )


@router.post("/threads/{thread_id}/runs/{run_id}/cancel")
async def api_cancel_thread_run(
    thread_id: str,
    run_id: str,
    session: dict[str, Any] = SESSION,
    wait: str = "0",
    action: str = "interrupt",
) -> Response:
    status_code, content, media_type = await proxy_dashboard_thread_run_cancel(
        thread_id,
        run_id,
        session["sub"],
        wait=wait,
        action=action,
        email=session.get("email"),
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.post("/threads/{thread_id}/cancel")
async def api_cancel_thread(
    thread_id: str,
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    return await cancel_dashboard_thread(thread_id, session["sub"], email=session.get("email"))


@router.delete("/threads/{thread_id}")
async def api_delete_thread(
    thread_id: str,
    session: dict[str, Any] = SESSION,
) -> Response:
    await delete_dashboard_thread(thread_id, session["sub"], email=session.get("email"))
    return Response(status_code=204)


@router.get("/threads/{thread_id}/state")
async def api_get_thread_state(
    thread_id: str,
    session: dict[str, Any] = SESSION,
) -> Response:
    timings: dict[str, float] = {}
    started = perf_counter()
    payload = await get_dashboard_thread_state(
        thread_id, session["sub"], email=session.get("email"), timings=timings
    )
    timings["total"] = (perf_counter() - started) * 1000
    header = server_timing_header(timings)
    logger.info("thread state timings thread_id=%s %s", thread_id, header)
    return JSONResponse(payload, headers={"Server-Timing": header})


@router.post("/threads/{thread_id}/stream/events")
async def api_thread_stream_events(
    thread_id: str,
    request: Request,
    session: dict[str, Any] = SESSION,
) -> StreamingResponse:
    body = await request.body()
    stream = await proxy_dashboard_thread_stream_events(
        thread_id,
        session["sub"],
        body,
        email=session.get("email"),
        content_type=request.headers.get("content-type", "application/json"),
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("/threads/{thread_id}/commands")
async def api_thread_commands(
    thread_id: str,
    request: Request,
    session: dict[str, Any] = SESSION,
) -> Response:
    body = await request.body()
    status_code, content, media_type = await proxy_dashboard_thread_commands(
        thread_id,
        session["sub"],
        body,
        email=session.get("email"),
        content_type=request.headers.get("content-type", "application/json"),
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.post("/threads/{thread_id}/history")
async def api_thread_history(
    thread_id: str,
    request: Request,
    session: dict[str, Any] = SESSION,
) -> Response:
    body = await request.body()
    status_code, content, media_type = await proxy_dashboard_thread_history(
        thread_id,
        session["sub"],
        body,
        email=session.get("email"),
        content_type=request.headers.get("content-type", "application/json"),
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.get("/threads/{thread_id}/stream")
async def api_stream_thread(
    thread_id: str,
    request: Request,
    session: dict[str, Any] = SESSION,
) -> StreamingResponse:
    last_event_id = request.headers.get("last-event-id")

    async def event_generator():
        async for chunk in stream_dashboard_thread(
            thread_id, session["sub"], email=session.get("email"), last_event_id=last_event_id
        ):
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
