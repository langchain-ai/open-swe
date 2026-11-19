"""HTTP API for dashboard threads."""

import json
import logging
import posixpath
from time import perf_counter
from typing import Annotated, Any, Literal, NoReturn
from uuid import UUID

import httpx
import httpx2
import orjson
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import ADMIN_DEP, SESSION_DEP, DashboardRead, session_is_admin
from openswe.dashboard.oauth import bind_audit_session, decode_upload_ticket
from openswe.dashboard.ttft import SUBMITTED_AT_HEADER, DashboardClient
from openswe.github.pull_request_checks import PullRequestState
from openswe.github.pull_request_context import PullRequestFixScope
from openswe.github.token_auth import bearer_github_token
from openswe.message_queue import QueuedPreview
from openswe.threads import browser, terminal
from openswe.threads.background_work import (
    BackgroundOutput,
    BackgroundWork,
    get_dashboard_thread_background_output,
    get_dashboard_thread_background_work,
)
from openswe.threads.diffs import (
    get_dashboard_thread_branch_diff,
    get_dashboard_thread_recovery_patch,
    get_dashboard_thread_working_tree_diff,
)
from openswe.threads.file_preview import (
    ERROR_HEADERS,
    PREVIEW_MAX_BYTES,
    ByteRange,
    FilePreviewGrant,
    PreviewFileType,
    PreviewLink,
)
from openswe.threads.files import (
    WorkspaceFileIndex,
    WorkspacePath,
    get_dashboard_thread_file_index,
    get_dashboard_thread_path,
)
from openswe.threads.handlers import (
    admin_cancel_dashboard_thread,
    cancel_dashboard_thread,
    cancel_machine_thread,
    continue_thread_privately,
    delete_dashboard_thread,
    get_dashboard_pull_request_checks,
    get_dashboard_thread,
    get_dashboard_thread_pull_request_context,
    get_dashboard_thread_pull_request_status,
    get_dashboard_thread_queued_messages,
    get_dashboard_thread_state,
    interrupt_transcript_turns,
    rename_dashboard_thread,
    resolve_all_dashboard_threads,
    resolve_dashboard_thread,
    share_thread,
)
from openswe.threads.listing import (
    SIDEBAR_ORDERS,
    DashboardThreadScope,
    SidebarThreadOrderBody,
    list_dashboard_pinned_threads,
    list_dashboard_thread_repos,
    list_dashboard_threads,
    list_dashboard_threads_page,
    pin_dashboard_thread,
    unpin_dashboard_thread,
)
from openswe.threads.machine_reads import machine_thread, machine_threads
from openswe.threads.pins import THREAD_PINS
from openswe.threads.principals import Principal, PrincipalDep
from openswe.threads.proxy import (
    proxy_dashboard_thread_commands,
    proxy_dashboard_thread_history,
    proxy_dashboard_thread_run_cancel,
    proxy_dashboard_thread_stream_events,
)
from openswe.threads.read_state import THREAD_READ_STATES
from openswe.threads.runs import (
    SessionCreateBody,
    ThreadCreateBody,
    ThreadRenameBody,
    ThreadResolveBody,
    create_dashboard_session,
)
from openswe.threads.runs_api import SdkRun, ThreadOnRuntime
from openswe.threads.runtimes import RUNTIMES
from openswe.threads.session_upload import UPLOAD_REQUEST_BODY, UploadStream, upload_session
from openswe.threads.store import MAC_THREAD, THREADS
from openswe.threads.summary import (
    ThreadReadPosition,
    _now_ms,
    _thread_run_id,
    assert_thread_readable,
)
from openswe.utils.timing import RequestTimings, phase, server_timing_header

logger = logging.getLogger(__name__)

router = APIRouter(tags=["threads"])


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


@router.post("/threads", status_code=201, response_model=dict[str, str])
async def api_create_session(
    body: Annotated[SessionCreateBody | ThreadCreateBody, Body(union_mode="left_to_right")],
    session: dict[str, str] = SESSION_DEP,
) -> dict[str, str] | Response:
    """Start a session from a prompt. The SDK's ``threads.create`` (a ``thread_id`` and no
    prompt) creates nothing: it gets back a thread the caller can read, and 404 otherwise."""
    if isinstance(body, ThreadCreateBody):
        return JSONResponse(await body.existing(session["sub"], email=session.get("email")))
    thread_id = await create_dashboard_session(body, session["sub"], email=session.get("email"))
    if body.start:
        status, content, _ = await proxy_dashboard_thread_commands(
            thread_id,
            session["sub"],
            json.dumps(
                {
                    "id": 1,
                    "method": "run.start",
                    "params": {"input": {"messages": [{"type": "human", "content": body.prompt}]}},
                }
            ).encode(),
            email=session.get("email"),
        )
        try:
            result: object = json.loads(content)
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(
                502, detail={"thread_id": thread_id, "error": "invalid run response"}
            ) from exc
        if (
            status >= 400
            or not isinstance(result, dict)
            or result.get("type") != "success"
            or not isinstance(result.get("result"), dict)
            or not result["result"].get("run_id")
        ):
            raise HTTPException(
                status if status >= 400 else 502,
                detail={"thread_id": thread_id, "error": result},
            )
    return {"thread_id": thread_id}


@router.post("/threads/uploads", openapi_extra=UPLOAD_REQUEST_BODY, response_model=None)
@audit_endpoint
async def api_upload_session(request: Request) -> dict[str, Any] | Response:
    code = bearer_github_token(request)
    if code is None:
        raise HTTPException(401, "send the upload token as a bearer token")
    ticket = decode_upload_ticket(code)
    bind_audit_session(request, {"sub": ticket.sub, "user_id": ticket.user_id})
    summary = await upload_session(UploadStream(request), ticket)
    if summary is None:
        return Response(status_code=204)
    return summary


@router.post("/threads/resolve-all")
@audit_endpoint
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
    owned: bool = False,
    all: bool = False,
    exclude_source: Annotated[list[str] | None, Query()] = None,
    session: dict[str, Any] = SESSION_DEP,
) -> list[dict[str, Any]]:
    if all and not session_is_admin(session):
        raise HTTPException(403, "admin only")
    return await list_dashboard_thread_repos(
        session["sub"],
        email=session.get("email"),
        include_resolved=include_resolved,
        include_automations=include_automations,
        owned=owned,
        include_all=all,
        exclude_sources=frozenset(exclude_source or ()),
    )


@router.put("/threads/order")
@audit_endpoint
async def api_reorder_sidebar_threads(
    body: SidebarThreadOrderBody,
    session: dict[str, str] = SESSION_DEP,
) -> dict[str, bool]:
    try:
        await SIDEBAR_ORDERS.reorder(session["sub"], body, email=session.get("email"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@router.get("/threads/pinned")
async def api_list_pinned_threads(
    reader: Annotated[DashboardRead, Depends(DashboardRead.open)],
) -> list[dict[str, Any]]:
    await reader.close()
    return await list_dashboard_pinned_threads(reader.principal.subject, email=reader.email)


class PinnedThreadOrderBody(BaseModel):
    thread_ids: list[str] = Field(max_length=1000)


@router.put("/threads/pinned/order")
@audit_endpoint
async def api_reorder_pinned_threads(
    body: PinnedThreadOrderBody,
    session: dict[str, str] = SESSION_DEP,
) -> dict[str, bool]:
    try:
        await THREAD_PINS.reorder(session["sub"], body.thread_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@router.post("/threads/{thread_id}/pin", status_code=204)
@audit_endpoint
async def api_pin_thread(
    thread_id: str,
    session: dict[str, Any] = SESSION_DEP,
) -> Response:
    await pin_dashboard_thread(thread_id, session["sub"])
    return Response(status_code=204)


@router.delete("/threads/{thread_id}/pin", status_code=204)
@audit_endpoint
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
    scope: DashboardThreadScope = "all",
    automation_id: str | None = None,
    bot: Annotated[str | None, Query(max_length=80)] = None,
    repo: str | None = None,
    ownerless: bool = False,
    owned: bool = False,
    exclude_source: Annotated[list[str] | None, Query()] = None,
    sort_by: Literal["created_at", "updated_at"] = "updated_at",
    hierarchy: bool = False,
    project: Annotated[str | None, Query(max_length=64)] = None,
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
        bot=bot,
        repo=repo,
        ownerless=ownerless,
        owned=owned,
        exclude_sources=frozenset(exclude_source or ()),
        sort_by=sort_by,
        hierarchy=hierarchy,
        project=project,
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


@router.get("/threads/{thread_id}/queued-messages", response_model=list[QueuedPreview])
async def api_get_thread_queued_messages(
    thread_id: str, session: dict[str, Any] = SESSION_DEP
) -> list[QueuedPreview]:
    return await get_dashboard_thread_queued_messages(
        thread_id, session["sub"], email=session.get("email")
    )


@router.get("/threads/{thread_id}/pull-request-context")
async def api_get_thread_pull_request_context(
    thread_id: str,
    repo_full_name: str,
    number: int,
    scope: PullRequestFixScope,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    return await get_dashboard_thread_pull_request_context(
        thread_id,
        session["sub"],
        repo_full_name=repo_full_name,
        number=number,
        scope=scope,
        email=session.get("email"),
    )


class ThreadReadState(BaseModel):
    viewed: bool
    response_id: str | None = Field(default=None, alias="responseId")


@router.put(
    "/threads/{thread_id}/read-state",
    response_model=ThreadReadState,
    response_model_exclude_unset=True,
)
async def api_set_thread_read_state(
    thread_id: str,
    body: ThreadReadState,
    session: dict[str, str] = SESSION_DEP,
) -> ThreadReadState:
    thread = await THREADS.get(thread_id)
    if thread is None:
        raise HTTPException(404, "thread not found")
    assert_thread_readable(thread.metadata, session["sub"], session.get("email"))
    latest = await RUNTIMES.runs_on(thread.runtime).latest_run(thread_id) if body.viewed else None
    if latest is not None and (
        latest.run_id != thread.metadata.get("latest_run_id")
        or latest.status != thread.metadata.get("latest_run_status")
    ):
        await THREADS.update(
            thread_id, {"latest_run_id": latest.run_id, "latest_run_status": latest.status}
        )
    run_id = _thread_run_id(thread.metadata, latest.run_id if latest else None)
    position = ThreadReadPosition(thread.metadata, None, session["sub"], run_id)
    guarded = "response_id" in body.model_fields_set
    if (
        not body.viewed
        or not guarded
        or not position.review
        or body.response_id == position.response_id
    ):
        await THREAD_READ_STATES.set(
            session["sub"],
            thread_id,
            viewed=body.viewed,
            run_id=run_id,
            viewed_at_ms=_now_ms(),
            response_id=body.response_id,
            acknowledge_current=not guarded or position.review,
        )
    position = ThreadReadPosition(
        thread.metadata,
        await THREAD_READ_STATES.saved(session["sub"], thread_id),
        session["sub"],
        run_id,
    )
    return ThreadReadState(viewed=position.viewed)


@router.get("/threads/{thread_id}")
async def api_get_thread(
    thread_id: str,
    principal: PrincipalDep,
) -> Response:
    if principal.machine:
        return JSONResponse(await machine_thread(thread_id, principal))
    timings: dict[str, float] = {}
    started = perf_counter()
    payload = await get_dashboard_thread(
        thread_id,
        principal.person,
        email=principal.email,
        timings=timings,
    )
    timings["total"] = (perf_counter() - started) * 1000
    return JSONResponse(payload, headers={"Server-Timing": server_timing_header(timings)})


router.include_router(terminal.router)
router.include_router(browser.router)


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


@router.get("/threads/{thread_id}/files")
async def api_get_thread_path(
    thread_id: str,
    path: str = "",
    session: dict[str, Any] = SESSION_DEP,
) -> WorkspacePath:
    return await get_dashboard_thread_path(
        thread_id, session["sub"], path, email=session.get("email")
    )


class FilePreviewBody(BaseModel):
    path: str


@router.post("/threads/{thread_id}/files/preview-url")
async def api_create_thread_file_preview(
    thread_id: str,
    body: FilePreviewBody,
    session: dict[str, object] = SESSION_DEP,
) -> PreviewLink:
    """A short-lived link to one file's bytes, or an HTML page's folder, for the Files panel."""
    return await FilePreviewGrant.mint(thread_id, session, body.path)


# No session: the link's token is the credential, and a sandboxed preview frame sends no cookies.
# Each request, a deck's every asset included, re-checks access and stats the file in the sandbox.
@router.get("/threads/{thread_id}/files/raw/{token}/{rest:path}")
async def api_get_thread_file_preview(
    thread_id: str, token: str, rest: str, request: Request
) -> Response:
    try:
        grant = FilePreviewGrant.decode(token, thread_id)
        workspace = await grant.open(request.url.path, request.method)
        file = await grant.stat(workspace, rest)
        file_type = PreviewFileType.of(file.path)
        span = ByteRange.of(request.headers.get("range"), file.size)
        headers = file_type.headers(posixpath.basename(file.path)) | span.headers()
        content = (
            await workspace.read(file, span.start, span.end, limit=PREVIEW_MAX_BYTES)
            if span.length
            else b""
        )
    except HTTPException as exc:
        raise HTTPException(
            exc.status_code, exc.detail, headers={**ERROR_HEADERS, **(exc.headers or {})}
        ) from exc
    return Response(
        content=content,
        status_code=206 if span.partial else 200,
        headers={**headers, "Content-Length": str(len(content))},
    )


@router.get("/threads/{thread_id}/background-work")
async def api_get_thread_background_work(
    thread_id: str, session: dict[str, Any] = SESSION_DEP
) -> BackgroundWork:
    return await get_dashboard_thread_background_work(
        thread_id, session["sub"], email=session.get("email")
    )


@router.get("/threads/{thread_id}/background-work/{task_id}/output")
async def api_get_thread_background_output(
    thread_id: str, task_id: str, session: dict[str, Any] = SESSION_DEP
) -> BackgroundOutput:
    return await get_dashboard_thread_background_output(
        thread_id, task_id, session["sub"], email=session.get("email")
    )


@router.get("/threads/{thread_id}/file-index")
async def api_get_thread_file_index(
    thread_id: str,
    session: dict[str, Any] = SESSION_DEP,
) -> WorkspaceFileIndex:
    return await get_dashboard_thread_file_index(
        thread_id, session["sub"], email=session.get("email")
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


@router.patch("/threads/{thread_id}")
@audit_endpoint
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


@router.post("/threads/{thread_id}/share")
@audit_endpoint
async def api_share_thread(
    thread_id: str,
    session: dict[str, str] = SESSION_DEP,
) -> dict[str, object]:
    return await share_thread(thread_id, session["sub"], email=session.get("email"))


@router.post(
    "/threads/{thread_id}/move", status_code=410, response_model=None, include_in_schema=False
)
async def api_move_thread(thread_id: str) -> NoReturn:
    """Every thread runs in the cloud: there is nowhere to move one."""
    raise HTTPException(410, MAC_THREAD)


@router.post("/threads/{thread_id}/continue-private")
@audit_endpoint
async def api_continue_thread_privately(
    thread_id: str,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    return await continue_thread_privately(thread_id, session["sub"], email=session.get("email"))


@router.post("/threads/{thread_id}/resolve")
@audit_endpoint
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
    reader = Principal.of_login(session["sub"], session.get("email"))
    thread = await ThreadOnRuntime.readable(thread_id, reader)
    params = httpx2.QueryParams(
        [
            ("limit", limit),
            ("offset", offset),
            *([("status", status)] if status else []),
            *(("select", field) for field in select or []),
        ]
    )
    return await thread.runs(f"/threads/{thread_id}/runs", params)


@router.post("/threads/{thread_id}/runs")
@audit_endpoint
async def api_create_thread_run(
    thread_id: str,
    body: SdkRun,
    request: Request,
    principal: PrincipalDep,
) -> Response:
    """Start a run as ``/commands`` starts a message, answered with the run, unstreamed."""
    return await body.create(thread_id, principal, request)


@router.get("/info")
async def api_runtime_info(_principal: PrincipalDep) -> Response:
    """MDA's ``/info``: the features the SDK turns on are MDA's, not this deployment's."""
    try:
        status_code, content, headers = await RUNTIMES.mda_runtime.info()
    except httpx2.TransportError as exc:
        raise HTTPException(502, "the runtime could not be reached") from exc
    return Response(content, status_code=status_code, headers=headers)


@router.post("/threads/{thread_id}/runs/stream")
@audit_endpoint
async def api_stream_thread_run(
    thread_id: str,
    body: SdkRun,
    request: Request,
    principal: PrincipalDep,
) -> Response:
    """Start a run as ``/commands`` starts a message, streamed in the runs API's format."""
    return await body.stream(thread_id, principal, request)


@router.get("/threads/{thread_id}/runs/running/stream")
async def api_join_running_thread_run(
    thread_id: str, request: Request, principal: PrincipalDep
) -> Response:
    """The run going on the thread, joined before a page opening it has read its head."""
    thread = await ThreadOnRuntime.readable(thread_id, principal)
    return await thread.join_running(request)


@router.get("/threads/{thread_id}/runs/{run_id}/stream")
async def api_join_thread_run(
    thread_id: str, run_id: UUID, request: Request, principal: PrincipalDep
) -> Response:
    thread = await ThreadOnRuntime.readable(thread_id, principal)
    return await thread.join(str(run_id), request)


@router.get("/threads/{thread_id}/runs/{run_id}")
async def api_get_thread_run(thread_id: str, run_id: UUID, principal: PrincipalDep) -> Response:
    thread = await ThreadOnRuntime.readable(thread_id, principal)
    return await thread.runs(f"/threads/{thread_id}/runs/{run_id}")


@router.get("/threads/{thread_id}/stream")
async def api_watch_thread(thread_id: str, request: Request, principal: PrincipalDep) -> Response:
    """The thread's runs as they start and end, for as long as a tab watches it."""
    thread = await ThreadOnRuntime.readable(thread_id, principal)
    return await thread.stream_as_sent(f"/threads/{thread_id}/stream", request)


@router.post("/threads/{thread_id}/runs/{run_id}/cancel")
@audit_endpoint
async def api_cancel_thread_run(
    thread_id: str,
    run_id: str,
    session: dict[str, Any] = SESSION_DEP,
    wait: str = "0",
    action: str = "interrupt",
    reason: str | None = None,
) -> Response:
    status_code, content, media_type = await proxy_dashboard_thread_run_cancel(
        thread_id,
        run_id,
        session["sub"],
        wait=wait,
        action=action,
        reason=reason,
        email=session.get("email"),
    )
    if status_code < 400:
        await interrupt_transcript_turns(thread_id, [run_id])
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.post("/threads/{thread_id}/cancel")
@audit_endpoint
async def api_cancel_thread(
    thread_id: str,
    principal: PrincipalDep,
) -> dict[str, Any]:
    if principal.machine:
        return await cancel_machine_thread(thread_id, principal)
    return await cancel_dashboard_thread(thread_id, principal.person, email=principal.email)


@router.post("/admin/threads/{thread_id}/cancel")
@audit_endpoint
async def admin_cancel_thread(
    thread_id: str,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> dict[str, Any]:
    return await admin_cancel_dashboard_thread(thread_id, _admin["sub"], email=_admin.get("email"))


@router.delete("/threads/{thread_id}")
@audit_endpoint
async def api_delete_thread(
    thread_id: str,
    session: dict[str, Any] = SESSION_DEP,
) -> Response:
    await delete_dashboard_thread(thread_id, session["sub"], email=session.get("email"))
    return Response(status_code=204)


@router.get("/threads/{thread_id}/state")
async def api_get_thread_state(
    request: Request,
    thread_id: str,
    principal: PrincipalDep,
    select: Annotated[list[str] | None, Query()] = None,
) -> Response:
    timings = RequestTimings.of(request)
    started = perf_counter()
    try:
        payload = await get_dashboard_thread_state(
            thread_id,
            principal.login or "",
            select=select,
            email=principal.email,
            timings=timings,
            principal=principal,
        )
    except (httpx.HTTPStatusError, httpx2.HTTPStatusError) as exc:
        # The runtime validates the read, `select` included, and an old thread the index
        # still lists may be gone from it: what it refuses goes back as it said.
        if exc.response.status_code not in (
            httpx.codes.UNPROCESSABLE_ENTITY,
            httpx.codes.NOT_FOUND,
        ):
            raise
        return Response(
            content=exc.response.content,
            status_code=exc.response.status_code,
            media_type=exc.response.headers.get("content-type"),
        )
    timings["total"] = (perf_counter() - started) * 1000
    with phase(timings, "encode"):
        body = orjson.dumps(payload)
    header = server_timing_header(timings)
    logger.info("thread state timings thread_id=%s %s", thread_id, header)
    return Response(body, media_type="application/json", headers={"Server-Timing": header})


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
@audit_endpoint
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
        client=DashboardClient.of_user_agent(request.headers.get("user-agent")),
        submitted_at=request.headers.get(SUBMITTED_AT_HEADER),
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.post("/threads/{thread_id}/history")
async def api_thread_history(
    thread_id: str,
    request: Request,
    session: dict[str, Any] = SESSION_DEP,
) -> Response:
    body = await request.body()
    timings: dict[str, float] = {}
    started = perf_counter()
    status_code, content, media_type = await proxy_dashboard_thread_history(
        thread_id,
        session["sub"],
        body,
        email=session.get("email"),
        content_type=request.headers.get("content-type", "application/json"),
        timings=timings,
    )
    timings["total"] = (perf_counter() - started) * 1000
    return Response(
        content=content,
        status_code=status_code,
        media_type=media_type,
        headers={"Server-Timing": server_timing_header(timings)},
    )
