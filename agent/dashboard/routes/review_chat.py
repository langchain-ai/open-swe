"""PR chat: a thin proxy in front of the sandbox-less ``chat`` graph.

The frontend points a LangGraph StreamProvider at the base
``/reviews/{owner}/{repo}/{pr_number}/chat``; the SDK then issues the
``/threads/{id}/{commands,stream/events,state,history}`` calls proxied here.
"""

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse

from ..authz import REPO_ACCESS, SESSION, RepoAccess
from ..review_chat_api import (
    delete_review_chat_thread,
    get_review_chat,
    list_review_chat_threads,
    proxy_review_chat_commands,
    proxy_review_chat_history,
    proxy_review_chat_state,
    proxy_review_chat_stream_events,
)

router = APIRouter()


@router.get("/reviews/{owner}/{repo}/{pr_number}/chat")
async def api_get_review_chat(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = SESSION,
    _access: RepoAccess = REPO_ACCESS,
) -> dict[str, Any]:
    return await get_review_chat(owner, repo, pr_number, session["sub"])


@router.get("/reviews/{owner}/{repo}/{pr_number}/chat/threads")
async def api_list_review_chat_threads(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = SESSION,
    _access: RepoAccess = REPO_ACCESS,
) -> dict[str, Any]:
    threads = await list_review_chat_threads(owner, repo, pr_number, session["sub"])
    return {"threads": threads}


@router.delete("/reviews/{owner}/{repo}/{pr_number}/chat/threads/{thread_id}")
async def api_delete_review_chat_thread(
    owner: str,
    repo: str,
    pr_number: int,
    thread_id: str,
    session: dict[str, Any] = SESSION,
    _access: RepoAccess = REPO_ACCESS,
) -> Response:
    await delete_review_chat_thread(owner, repo, pr_number, session["sub"], thread_id)
    return Response(status_code=204)


@router.post("/reviews/{owner}/{repo}/{pr_number}/chat/threads/{thread_id}/commands")
async def api_review_chat_commands(
    owner: str,
    repo: str,
    pr_number: int,
    thread_id: str,
    request: Request,
    session: dict[str, Any] = SESSION,
    _access: RepoAccess = REPO_ACCESS,
) -> Response:
    body = await request.body()
    status_code, content, media_type = await proxy_review_chat_commands(
        owner,
        repo,
        pr_number,
        session["sub"],
        thread_id,
        body,
        content_type=request.headers.get("content-type", "application/json"),
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.post("/reviews/{owner}/{repo}/{pr_number}/chat/threads/{thread_id}/stream/events")
async def api_review_chat_stream_events(
    owner: str,
    repo: str,
    pr_number: int,
    thread_id: str,
    request: Request,
    session: dict[str, Any] = SESSION,
    _access: RepoAccess = REPO_ACCESS,
) -> StreamingResponse:
    body = await request.body()
    stream = await proxy_review_chat_stream_events(
        owner,
        repo,
        pr_number,
        session["sub"],
        thread_id,
        body,
        content_type=request.headers.get("content-type", "application/json"),
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/reviews/{owner}/{repo}/{pr_number}/chat/threads/{thread_id}/state")
async def api_review_chat_state(
    owner: str,
    repo: str,
    pr_number: int,
    thread_id: str,
    session: dict[str, Any] = SESSION,
    _access: RepoAccess = REPO_ACCESS,
) -> Response:
    status_code, content, media_type = await proxy_review_chat_state(
        owner, repo, pr_number, session["sub"], thread_id
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.post("/reviews/{owner}/{repo}/{pr_number}/chat/threads/{thread_id}/history")
async def api_review_chat_history(
    owner: str,
    repo: str,
    pr_number: int,
    thread_id: str,
    request: Request,
    session: dict[str, Any] = SESSION,
    _access: RepoAccess = REPO_ACCESS,
) -> Response:
    body = await request.body()
    status_code, content, media_type = await proxy_review_chat_history(
        owner,
        repo,
        pr_number,
        session["sub"],
        thread_id,
        body,
        content_type=request.headers.get("content-type", "application/json"),
    )
    return Response(content=content, status_code=status_code, media_type=media_type)
