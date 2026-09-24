"""HTTP API for dashboard threads."""

import logging
from time import perf_counter
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from agent.config import ENV
from agent.dashboard.deps import ADMIN_DEP, SESSION_DEP, session_is_admin
from agent.dashboard.user_preferences import get_user_preferences
from agent.github.pull_request_checks import PullRequestState
from agent.threads import terminal
from agent.threads.diffs import (
    get_dashboard_thread_branch_diff,
    get_dashboard_thread_recovery_patch,
    get_dashboard_thread_working_tree_diff,
)
from agent.threads.feedback import feedback_router
from agent.threads.handlers import (
    admin_cancel_dashboard_thread,
    cancel_dashboard_thread,
    cancel_machine_thread,
    continue_thread_privately,
    delete_dashboard_thread,
    get_dashboard_pull_request_checks,
    get_dashboard_thread,
    get_dashboard_thread_pull_request_context,
    get_dashboard_thread_pull_request_status,
    get_dashboard_thread_state,
    interrupt_transcript_turns,
    rename_dashboard_thread,
    resolve_all_dashboard_threads,
    resolve_dashboard_thread,
    send_dashboard_message,
)
from agent.threads.listing import (
    list_dashboard_pinned_threads,
    list_dashboard_thread_repos,
    list_dashboard_threads,
    list_dashboard_threads_page,
    pin_dashboard_thread,
    unpin_dashboard_thread,
)
from agent.threads.machine_reads import machine_thread, machine_threads
from agent.threads.principals import PrincipalDep
from agent.threads.proxy import (
    proxy_dashboard_thread_commands,
    proxy_dashboard_thread_history,
    proxy_dashboard_thread_run_cancel,
    proxy_dashboard_thread_run_enqueue,
    proxy_dashboard_thread_runs_list,
    proxy_dashboard_thread_stream_events,
)
from agent.threads.runs import (
    ThreadMessageBody,
    ThreadRenameBody,
    ThreadResolveBody,
)
from agent.utils.langsmith import get_langsmith_trace_url
from agent.utils.timing import server_timing_header

logger = logging.getLogger(__name__)

router = APIRouter(tags=["threads"])
router.include_router(feedback_router)


@router.get("/me/local-trace-url/{thread_id}")
async def api_get_local_trace_url(
    thread_id: UUID,
    session: dict[str, str] = SESSION_DEP,
) -> dict[str, str | None]:
    preferences = await get_user_preferences(session["sub"])
    project = preferences["local_tracing_project"] or ENV.LANGSMITH_PROJECT.get()
    return {"trace_url": await get_langsmith_trace_url(str(thread_id), project_name=project)}


@router.get("/threads")
async def api_list_threads(
    principal: PrincipalDep,
    all: bool = False,
    limit: int = 25,
) -> list[dict[str, Any]]:
    if principal.machine:
        return await machine_threads(principal, limit=limit)
    if all and not principal.admin:
        raise HTTPException(403, "admin only")
    return await list_dashboard_threads(principal.person, email=principal.email, include_all=all)


@router.post("/threads/resolve-all")
async def api_resolve_all_threads(
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, int]:
    return {
        "resolved": await resolve_all_dashboard_threads(session["sub"], email=session.get("email"))
    }


@router.get("/threads/repos")
async def api_list_thread_repos(
    include_resolved: bool = False,
    include_automations: bool = False,
    all: bool = False,
    session: dict[str, Any] = SESSION_DEP,
) -> list[dict[str, Any]]:
    if all and not session_is_admin(session):
        raise HTTPException(403, "admin only")
    return await list_dashboard_thread_repos(
        session["sub"],
        email=session.get("email"),
        include_resolved=include_resolved,
        include_automations=include_automations,
        include_all=all,
    )


@router.get("/threads/pinned")
async def api_list_pinned_threads(
    session: dict[str, Any] = SESSION_DEP,
) -> list[dict[str, Any]]:
    return await list_dashboard_pinned_threads(session["sub"], email=session.get("email"))


@router.post("/threads/{thread_id}/pin", status_code=204)
async def api_pin_thread(
    thread_id: str,
    session: dict[str, Any] = SESSION_DEP,
) -> Response:
    await pin_dashboard_thread(thread_id, session["sub"])
    return Response(status_code=204)


@router.delete("/threads/{thread_id}/pin", status_code=204)
async def api_unpin_thread(
    thread_id: str,
    session: dict[str, Any] = SESSION_DEP,
) -> Response:
    await unpin_dashboard_thread(thread_id, session["sub"])
    return Response(status_code=204)


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
    repo: str | None = None,
    ownerless: bool = False,
    sort_by: Literal["created_at", "updated_at"] = "updated_at",
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    if all and not session_is_admin(session):
        raise HTTPException(403, "admin only")
    if repo and ownerless:
        raise HTTPException(400, "repo and ownerless are mutually exclusive")
    if repo:
        owner, separator, name = repo.strip().partition("/")
        if not separator or not owner or not name or "/" in name:
            raise HTTPException(400, "repo must be owner/name")
        repo = f"{owner}/{name}"
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
        repo=repo,
        ownerless=ownerless,
        sort_by=sort_by,
    )


class PullRequestChecksRef(BaseModel):
    repoFullName: str = Field(max_length=140)
    number: int = Field(ge=1)


class PullRequestChecksRequest(BaseModel):
    pullRequests: list[PullRequestChecksRef] = Field(default_factory=list, max_length=50)


@router.post("/threads/pull-request-checks")
async def api_get_pull_request_checks(
    payload: PullRequestChecksRequest,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, PullRequestState]:
    return await get_dashboard_pull_request_checks(
        [ref.model_dump() for ref in payload.pullRequests], session["sub"]
    )


@router.get("/threads/{thread_id}/pull-request-status")
async def api_get_thread_pull_request_status(
    thread_id: str,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    return await get_dashboard_thread_pull_request_status(
        thread_id,
        session["sub"],
        email=session.get("email"),
    )


@router.get("/threads/{thread_id}/pull-request-context")
async def api_get_thread_pull_request_context(
    thread_id: str,
    repo_full_name: str,
    number: int,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    return await get_dashboard_thread_pull_request_context(
        thread_id,
        session["sub"],
        repo_full_name=repo_full_name,
        number=number,
        email=session.get("email"),
    )


@router.get("/threads/{thread_id}")
async def api_get_thread(
    thread_id: str,
    principal: PrincipalDep,
    mark_viewed: bool = True,
) -> Response:
    if principal.machine:
        return JSONResponse(await machine_thread(thread_id, principal))
    timings: dict[str, float] = {}
    started = perf_counter()
    payload = await get_dashboard_thread(
        thread_id,
        principal.person,
        email=principal.email,
        mark_viewed=mark_viewed,
        timings=timings,
    )
    timings["total"] = (perf_counter() - started) * 1000
    return JSONResponse(payload, headers={"Server-Timing": server_timing_header(timings)})


router.include_router(terminal.router)


@router.get("/threads/{thread_id}/recovery.patch")
async def api_get_thread_recovery_patch(
    thread_id: str,
    session: dict[str, Any] = SESSION_DEP,
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


@router.get("/threads/{thread_id}/working-tree-diff")
async def api_get_thread_working_tree_diff(
    thread_id: str,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    return await get_dashboard_thread_working_tree_diff(
        thread_id, session["sub"], email=session.get("email")
    )


@router.get("/threads/{thread_id}/branch-diff")
async def api_get_thread_branch_diff(
    thread_id: str,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    return await get_dashboard_thread_branch_diff(
        thread_id,
        session["sub"],
        email=session.get("email"),
    )


# The pre-branch-diff name, kept for desktop bundles already in the wild.
@router.get("/threads/{thread_id}/pr-diff")
async def api_get_thread_pr_diff(
    thread_id: str,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    return await get_dashboard_thread_branch_diff(
        thread_id,
        session["sub"],
        email=session.get("email"),
    )


@router.post("/threads/{thread_id}/messages")
async def api_send_thread_message(
    thread_id: str,
    body: ThreadMessageBody,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    return await send_dashboard_message(thread_id, session["sub"], body, email=session.get("email"))


@router.patch("/threads/{thread_id}")
async def api_rename_thread(
    thread_id: str,
    body: ThreadRenameBody,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    return await rename_dashboard_thread(
        thread_id,
        session["sub"],
        title=body.title,
        email=session.get("email"),
    )


@router.post("/threads/{thread_id}/continue-private")
async def api_continue_thread_privately(
    thread_id: str,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    return await continue_thread_privately(thread_id, session["sub"], email=session.get("email"))


@router.post("/threads/{thread_id}/resolve")
async def api_resolve_thread(
    thread_id: str,
    body: ThreadResolveBody,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    return await resolve_dashboard_thread(
        thread_id,
        session["sub"],
        resolved=body.resolved,
        email=session.get("email"),
    )


@router.get("/threads/{thread_id}/runs")
async def api_list_thread_runs(
    thread_id: str,
    limit: int = 10,
    offset: int = 0,
    status: str | None = None,
    select: Annotated[list[str] | None, Query()] = None,
    session: dict[str, Any] = SESSION_DEP,
) -> Response:
    status_code, content, media_type = await proxy_dashboard_thread_runs_list(
        thread_id,
        session["sub"],
        limit=limit,
        offset=offset,
        status=status,
        select=select,
        email=session.get("email"),
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.post("/threads/{thread_id}/runs")
async def api_create_thread_run(
    thread_id: str,
    request: Request,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    body = await request.body()
    return await proxy_dashboard_thread_run_enqueue(
        thread_id,
        session["sub"],
        body,
        email=session.get("email"),
        content_type=request.headers.get("content-type", "application/json"),
    )


@router.post("/threads/{thread_id}/runs/{run_id}/cancel")
async def api_cancel_thread_run(
    thread_id: str,
    run_id: str,
    session: dict[str, Any] = SESSION_DEP,
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
    if status_code < 400:
        await interrupt_transcript_turns(thread_id, [run_id])
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.post("/threads/{thread_id}/cancel")
async def api_cancel_thread(
    thread_id: str,
    principal: PrincipalDep,
) -> dict[str, Any]:
    if principal.machine:
        return await cancel_machine_thread(thread_id, principal)
    return await cancel_dashboard_thread(thread_id, principal.person, email=principal.email)


@router.post("/admin/threads/{thread_id}/cancel")
async def admin_cancel_thread(
    thread_id: str,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> dict[str, Any]:
    return await admin_cancel_dashboard_thread(thread_id, _admin["sub"], email=_admin.get("email"))


@router.delete("/threads/{thread_id}")
async def api_delete_thread(
    thread_id: str,
    session: dict[str, Any] = SESSION_DEP,
) -> Response:
    await delete_dashboard_thread(thread_id, session["sub"], email=session.get("email"))
    return Response(status_code=204)


@router.get("/threads/{thread_id}/state")
async def api_get_thread_state(
    thread_id: str,
    session: dict[str, Any] = SESSION_DEP,
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
    principal: PrincipalDep,
) -> StreamingResponse:
    body = await request.body()
    stream = await proxy_dashboard_thread_stream_events(
        thread_id,
        principal.login or "",
        body,
        email=principal.email,
        content_type=request.headers.get("content-type", "application/json"),
        principal=principal,
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
    principal: PrincipalDep,
) -> Response:
    """Every way a thread is started or continued, whoever is asking.

    The dashboard, an API key and a federated workflow all post the same command
    here; what differs is the thread the first one stamps.
    """
    body = await request.body()
    status_code, content, media_type = await proxy_dashboard_thread_commands(
        thread_id,
        principal.login or "",
        body,
        email=principal.email,
        content_type=request.headers.get("content-type", "application/json"),
        principal=principal,
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.post("/threads/{thread_id}/history")
async def api_thread_history(
    thread_id: str,
    request: Request,
    session: dict[str, Any] = SESSION_DEP,
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
