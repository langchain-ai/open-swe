"""Capability-authenticated discovery and invocation of thread tools."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from agent.sandboxes.tool_access import (
    TOOLS_HEADER,
    TOOLS_PATH,
    ToolAccess,
    authenticate_tool_access,
)
from agent.sandboxes.tool_runtime import ToolDescription, load_tool_surface

logger = logging.getLogger(__name__)
router = APIRouter(prefix=TOOLS_PATH, tags=["sandbox-tools"])
MAX_REQUEST_BYTES = 1024 * 1024


async def require_tool_access(request: Request, response: Response) -> ToolAccess:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    token = request.headers.get(TOOLS_HEADER) or request.query_params.get("token")
    return await authenticate_tool_access(token)


Access = Annotated[ToolAccess, Depends(require_tool_access)]


class InvokeToolBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=256)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class ToolList(BaseModel):
    tools: list[ToolDescription]
    total: int


@router.get("", response_model=ToolList)
@router.get("/list", response_model=ToolList)
@router.get("/search", response_model=ToolList)
async def list_tools(
    access: Access,
    q: Annotated[str, Query(max_length=512)] = "",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> ToolList:
    surface, _, _ = await load_tool_surface(access.thread_id)
    tools = surface.catalog(q)
    return ToolList(tools=tools[offset : offset + limit], total=len(tools))


@router.post("")
@router.post("/invoke")
async def invoke_tool(request: Request, access: Access) -> dict[str, JsonValue]:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_REQUEST_BYTES:
            raise HTTPException(413, "Tool request is too large")
    try:
        call = InvokeToolBody.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(422, "Expected name and arguments") from exc
    surface, config, state = await load_tool_surface(access.thread_id)
    try:
        return await surface.invoke(access.thread_id, config, state, call.name, call.arguments)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Sandbox tool invocation failed", extra={"tool_name": call.name})
        raise HTTPException(
            500, "Tool invocation failed; inspect server logs before retrying"
        ) from None
