"""Fleet chats through the caller's connected LangSmith account.

The dashboard's LangGraph SDK client talks to these routes, which forward an allowlisted
subset of Fleet's LangGraph API to LangSmith with the caller's own OAuth token. LangSmith acts
in the workspace the person chose when connecting, which the token carries. Fleet stamps
threads with the caller's LangSmith user and filters reads by it, so each person sees only
their own chats.
"""

import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.langsmith_oauth import (
    LangSmithOAuthError,
    langsmith_access_token,
    langsmith_issuer,
)
from openswe.users import User

router = APIRouter(prefix="/fleet-chat", tags=["fleet-chat"])
_ID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
_PATHS = {
    "GET": rf"threads/{_ID}(?:/state|/runs(?:/{_ID}(?:/stream)?)?)?",
    "POST": rf"threads(?:/search|/{_ID}/runs(?:/stream|/{_ID}/cancel)?)?",
    "PATCH": rf"threads/{_ID}",
}
_MAX_BODY = 1024 * 1024


async def _client(login: str) -> httpx.AsyncClient:
    if not (await User.preferences_for_login(login)).experimental_fleet:
        raise HTTPException(403, "Turn on Fleet in Settings → Experiments.")
    try:
        token = await langsmith_access_token(login)
    except LangSmithOAuthError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    if not token:
        raise HTTPException(401, "Connect LangSmith to use Fleet.")
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
        detail = "Reconnect LangSmith to use Fleet."
    elif status == 403:
        detail = "Your LangSmith workspace does not allow you to use Fleet."
    elif status == 404:
        detail = "Chat not found."
    raise HTTPException(status if status in {401, 403, 404, 409, 422, 429} else 502, detail)


@router.patch("/{path:path}")
@router.post("/{path:path}")
@router.get("/{path:path}")
async def proxy(
    path: str, request: Request, session: dict[str, Any] = SESSION_DEP
) -> StreamingResponse:
    if not re.fullmatch(_PATHS[request.method], path):
        raise HTTPException(404, "Unsupported Fleet operation.")
    body = await request.body() if request.method != "GET" else None
    if body is not None and len(body) > _MAX_BODY:
        raise HTTPException(413, "Message is too large.")
    client = await _client(session["sub"])
    try:
        headers = {"Content-Type": "application/json"}
        if last_event_id := request.headers.get("last-event-id"):
            headers["Last-Event-ID"] = last_event_id
        upstream = client.build_request(
            request.method,
            f"/v1/fleet/lg/{path}",
            params=httpx.QueryParams(str(request.query_params)),
            content=body,
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

    headers = {"Cache-Control": "no-store"}
    if location := response.headers.get("content-location"):
        headers["Content-Location"] = location
    return StreamingResponse(
        chunks(),
        status_code=response.status_code,
        media_type=response.headers.get("content-type", "application/json"),
        headers=headers,
    )
