"""Personal Fleet conversations through the connected LangSmith account."""

from collections.abc import AsyncIterator
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, JsonValue, TypeAdapter

from openswe.dashboard.langsmith_oauth import (
    LangSmithOAuthError,
    langsmith_access_token,
    langsmith_issuer,
)
from openswe.dashboard.oauth import require_session

router = APIRouter(prefix="/fleet-chat", tags=["fleet-chat"])
ASSISTANT_ID = "47c35ef5-979e-5ab8-8dce-a69ded2244ab"
OWNER_KEY = "open_swe_chat_owner"
_json = TypeAdapter(JsonValue)


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=32000)


class FleetChat:
    def __init__(self, login: str, http: httpx.AsyncClient) -> None:
        self.login = login
        self.http = http

    async def request(
        self, method: str, path: str, body: dict[str, JsonValue] | None = None
    ) -> JsonValue:
        try:
            response = await self.http.request(method, path, json=body)
        except httpx.HTTPError as exc:
            raise HTTPException(502, "LangSmith could not be reached. Please retry.") from exc
        if response.is_error:
            status = response.status_code
            detail = "LangSmith could not complete the request. Please retry."
            if status == 401:
                detail = "Reconnect LangSmith in Settings → Connections."
            elif status == 403:
                detail = "Your LangSmith account cannot access Chat in this workspace."
            elif status == 404:
                detail = "Chat not found."
            raise HTTPException(status if status in {401, 403, 404, 409, 429} else 502, detail)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return _json.validate_python(response.json())
        except ValueError as exc:
            raise HTTPException(502, "LangSmith returned an unreadable response.") from exc

    async def thread(self, thread_id: UUID) -> dict[str, JsonValue]:
        data = await self.request("GET", f"/v1/fleet/lg/threads/{thread_id}")
        metadata = data.get("metadata") if isinstance(data, dict) else None
        if (
            not isinstance(data, dict)
            or not isinstance(metadata, dict)
            or metadata.get(OWNER_KEY) != self.login
            or metadata.get("assistant_id") != ASSISTANT_ID
        ):
            raise HTTPException(404, "Chat not found.")
        return data


async def fleet_client(request: Request) -> AsyncIterator[FleetChat]:
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
    async with httpx.AsyncClient(
        base_url=langsmith_issuer(),
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
        follow_redirects=False,
    ) as http:
        yield FleetChat(login, http)


Client = Annotated[FleetChat, Depends(fleet_client)]


async def workspace_client(workspace_id: UUID, client: Client) -> FleetChat:
    workspaces = await client.request("GET", "/workspaces")
    if not isinstance(workspaces, list) or not any(
        isinstance(workspace, dict)
        and workspace.get("id") == str(workspace_id)
        and workspace.get("data_plane_url") is None
        for workspace in workspaces
    ):
        raise HTTPException(403, "Choose an accessible LangSmith SaaS workspace.")
    client.http.headers["X-Tenant-Id"] = str(workspace_id)
    return client


WorkspaceClient = Annotated[FleetChat, Depends(workspace_client)]


@router.get("/workspaces")
async def workspaces(client: Client) -> JsonValue:
    data = await client.request("GET", "/workspaces")
    if not isinstance(data, list):
        raise HTTPException(502, "LangSmith returned an unreadable workspace list.")
    return [
        {"id": item.get("id"), "name": item.get("display_name")}
        for item in data
        if isinstance(item, dict) and item.get("data_plane_url") is None
    ]


@router.get("/{workspace_id}/threads")
async def threads(client: WorkspaceClient) -> JsonValue:
    return await client.request(
        "POST",
        "/v1/fleet/lg/threads/search",
        {
            "metadata": {OWNER_KEY: client.login, "assistant_id": ASSISTANT_ID},
            "limit": 50,
            "sort_by": "updated_at",
            "sort_order": "desc",
        },
    )


@router.post("/{workspace_id}/threads")
async def create_thread(client: WorkspaceClient) -> JsonValue:
    return await client.request(
        "POST",
        "/v1/fleet/lg/threads",
        {"metadata": {OWNER_KEY: client.login, "assistant_id": ASSISTANT_ID}},
    )


@router.get("/{workspace_id}/threads/{thread_id}")
async def thread(thread_id: UUID, client: WorkspaceClient) -> JsonValue:
    return await client.thread(thread_id)


@router.post("/{workspace_id}/threads/{thread_id}/messages")
async def send_message(thread_id: UUID, body: Message, client: WorkspaceClient) -> JsonValue:
    await client.thread(thread_id)
    if not body.text.strip():
        raise HTTPException(422, "Enter a message.")
    return await client.request(
        "POST",
        f"/v1/fleet/lg/threads/{thread_id}/runs",
        {
            "assistant_id": ASSISTANT_ID,
            "input": {"messages": [{"role": "user", "content": body.text}]},
            "multitask_strategy": "reject",
            "stream_mode": ["values"],
            "stream_resumable": True,
        },
    )


@router.get("/{workspace_id}/threads/{thread_id}/runs/{run_id}/stream")
async def stream_run(thread_id: UUID, run_id: UUID, client: WorkspaceClient) -> StreamingResponse:
    await client.thread(thread_id)
    upstream = client.http.build_request(
        "GET",
        f"/v1/fleet/lg/threads/{thread_id}/runs/{run_id}/stream",
        params={"stream_mode": "values", "cancel_on_disconnect": "0"},
        timeout=httpx.Timeout(30.0, read=300.0),
    )
    try:
        response = await client.http.send(upstream, stream=True)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Could not connect to Fleet’s reply.") from exc
    if response.is_error:
        await response.aclose()
        raise HTTPException(502, "Could not connect to Fleet’s reply. Reopen the chat.")

    async def events() -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.post("/{workspace_id}/threads/{thread_id}/runs/{run_id}/{action}")
async def stop_run(
    thread_id: UUID, run_id: UUID, action: Literal["cancel"], client: WorkspaceClient
) -> JsonValue:
    await client.thread(thread_id)
    return await client.request("POST", f"/v1/fleet/lg/threads/{thread_id}/runs/{run_id}/{action}")
