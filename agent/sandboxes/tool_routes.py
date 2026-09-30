"""Capability-authenticated discovery and invocation of thread tools."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel
from starlette.types import Message, Receive, Scope, Send

from agent.sandboxes.tool_access import (
    TOOLS_HEADER,
    TOOLS_PATH,
    ToolAccess,
    authenticate_tool_access,
)
from agent.sandboxes.tool_models import ToolArguments, ToolDescription, ToolResult

logger = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 1024 * 1024


class ToolRoute(APIRoute):
    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        size = 0

        async def limited_receive() -> Message:
            nonlocal size
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > MAX_REQUEST_BYTES:
                    raise HTTPException(413, "Tool request is too large")
            return message

        await super().handle(scope, limited_receive, send)


router = APIRouter(prefix=TOOLS_PATH, tags=["sandbox-tools"], route_class=ToolRoute)


async def require_tool_access(request: Request, response: Response) -> ToolAccess:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    token = request.headers.get(TOOLS_HEADER) or request.query_params.get("token")
    return await authenticate_tool_access(token)


Access = Annotated[ToolAccess, Depends(require_tool_access)]


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
    from agent.sandboxes.tool_runtime import load_tool_surface

    surface, _, _ = await load_tool_surface(access.thread_id)
    tools = surface.catalog(q)
    return ToolList(tools=tools[offset : offset + limit], total=len(tools))


@router.post("/invoke/{tool_name}", response_model=ToolResult)
async def invoke_tool(
    tool_name: Annotated[str, Path(min_length=1, max_length=256)],
    arguments: ToolArguments,
    access: Access,
) -> ToolResult:
    from agent.sandboxes.tool_runtime import load_tool_surface

    surface, config, state = await load_tool_surface(access.thread_id)
    try:
        result = await surface.invoke(access.thread_id, config, state, tool_name, arguments.root)
        return ToolResult.model_validate(result)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Sandbox tool invocation failed", extra={"tool_name": tool_name})
        raise HTTPException(
            500, "Tool invocation failed; inspect server logs before retrying"
        ) from None
