"""Authenticate the Fleet SDK without exposing personal OAuth credentials."""

import re
from collections.abc import AsyncIterator
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from openswe.dashboard.langsmith_oauth import (
    LangSmithOAuthError,
    langsmith_access_token,
    langsmith_issuer,
)
from openswe.dashboard.oauth import require_session

router = APIRouter(prefix="/fleet-chat", tags=["fleet-chat"])
_ID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
_PATHS = {
    "GET": rf"threads/{_ID}(?:/state|/runs(?:/{_ID}(?:/stream)?)?)?",
    "POST": rf"threads(?:/search|/{_ID}/runs(?:/{_ID}/cancel)?)?",
}


async def _client(request: Request) -> httpx.AsyncClient:
    session = require_session(request)
    login = session.get("sub")
    if not isinstance(login, str):
        raise HTTPException(401, "Not authenticated.")
    try:
        token = await langsmith_access_token(login)
    except LangSmithOAuthError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    if not token:
        raise HTTPException(401, "Connect LangSmith in Settings → Connections to use Chat.")
    return httpx.AsyncClient(
        base_url=langsmith_issuer(),
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(30.0, read=300.0),
        follow_redirects=False,
    )


def _check(response: httpx.Response) -> None:
    if response.is_success:
        return
    status = response.status_code
    detail = "LangSmith could not complete this request."
    if status == 401:
        detail = "Reconnect LangSmith in Settings → Connections."
    elif status == 403:
        detail = "Your LangSmith account cannot access this workspace or conversation."
    raise HTTPException(status if status in {401, 403, 404, 409, 422, 429} else 502, detail)


@router.get("/workspaces")
async def workspaces(request: Request) -> list[dict[str, str]]:
    try:
        async with await _client(request) as client:
            response = await client.get("/workspaces")
            _check(response)
            return [
                {"id": item["id"], "name": item["display_name"]}
                for item in response.json()
                if item.get("data_plane_url") is None
            ]
    except httpx.HTTPError as exc:
        raise HTTPException(502, "LangSmith could not be reached.") from exc


@router.post("/{workspace_id}/{path:path}")
@router.get("/{workspace_id}/{path:path}")
async def proxy(workspace_id: UUID, path: str, request: Request) -> StreamingResponse:
    if not re.fullmatch(_PATHS[request.method], path):
        raise HTTPException(404, "Unsupported Fleet SDK operation.")
    client = await _client(request)
    try:
        membership = await client.get("/workspaces")
        _check(membership)
        if not any(
            item.get("id") == str(workspace_id) and item.get("data_plane_url") is None
            for item in membership.json()
        ):
            raise HTTPException(403, "Choose an accessible LangSmith SaaS workspace.")
        headers = {"X-Tenant-Id": str(workspace_id), "Content-Type": "application/json"}
        if last_event_id := request.headers.get("last-event-id"):
            headers["Last-Event-ID"] = last_event_id
        upstream = client.build_request(
            request.method,
            f"/v1/fleet/lg/{path}",
            params=httpx.QueryParams(str(request.query_params)),
            content=request.stream() if request.method == "POST" else None,
            headers=headers,
        )
        response = await client.send(upstream, stream=True)
        if not response.is_success:
            await response.aclose()
            _check(response)
    except (httpx.HTTPError, HTTPException) as exc:
        await client.aclose()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(502, "LangSmith could not be reached.") from exc

    async def chunks() -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    return StreamingResponse(
        chunks(),
        status_code=response.status_code,
        media_type=response.headers.get("content-type", "application/json"),
        headers={"Cache-Control": "no-store"},
    )
