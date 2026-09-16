"""Dashboard API for workspace-wide and per-user MCP connections."""

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from agent.dashboard.deps import ADMIN_DEP, SESSION_DEP
from agent.mcp.models import (
    MCPConnection,
    MCPConnectionPublic,
    MCPConnectionUpdate,
    MCPToolDescription,
)
from agent.mcp.user import (
    delete_user_mcp,
    discover_user_mcp,
    get_user_mcp,
    list_user_mcps,
    save_user_mcp,
)
from agent.mcp.workspace import (
    MCPRoute,
    delete_workspace_mcp,
    get_workspace_mcp,
    list_workspace_mcps,
    save_workspace_mcp,
)
from agent.tool_loaders.workspace_mcp import discover_workspace_mcp
from agent.workspaces.store import slugify


def _reveal_mcp_headers(record: MCPConnection | None) -> JSONResponse:
    if record is None:
        raise HTTPException(404, "MCP connection not found")
    try:
        headers = record.connection_headers()
    except ValueError:
        raise HTTPException(400, "MCP authentication headers could not be decrypted") from None
    return JSONResponse(content=headers, headers={"Cache-Control": "no-store"})


def _normalized_workspace(raw: str) -> str:
    try:
        return slugify(raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


workspace_mcp_router = APIRouter(route_class=MCPRoute)


@workspace_mcp_router.get("/workspaces/{workspace}/mcps", response_model=list[MCPConnectionPublic])
async def api_list_workspace_mcps(
    workspace: str, _admin: dict[str, Any] = ADMIN_DEP
) -> list[dict[str, Any]]:
    return await list_workspace_mcps(_normalized_workspace(workspace))


@workspace_mcp_router.put("/workspaces/{workspace}/mcps/{name}", response_model=MCPConnectionPublic)
async def api_save_workspace_mcp(
    workspace: str,
    name: str,
    update: MCPConnectionUpdate,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> dict[str, Any]:
    try:
        return await save_workspace_mcp(_normalized_workspace(workspace), name, update)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@workspace_mcp_router.delete("/workspaces/{workspace}/mcps/{name}", status_code=204)
async def api_delete_workspace_mcp(
    workspace: str, name: str, _admin: dict[str, Any] = ADMIN_DEP
) -> None:
    await delete_workspace_mcp(_normalized_workspace(workspace), name)


@workspace_mcp_router.post("/workspaces/{workspace}/mcps/{name}/headers/reveal")
async def api_reveal_workspace_mcp_headers(
    workspace: str,
    name: str,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> JSONResponse:
    return _reveal_mcp_headers(await get_workspace_mcp(_normalized_workspace(workspace), name))


@workspace_mcp_router.post(
    "/workspaces/{workspace}/mcps/{name}/discover", response_model=list[MCPToolDescription]
)
async def api_discover_workspace_mcp(
    workspace: str,
    name: str,
    update: MCPConnectionUpdate | None = None,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> list[dict[str, str]]:
    try:
        return await discover_workspace_mcp(_normalized_workspace(workspace), name, update)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


user_mcp_router = APIRouter(route_class=MCPRoute)


@user_mcp_router.get("/my-mcps", response_model=list[MCPConnectionPublic])
async def api_list_my_mcps(session: dict[str, Any] = SESSION_DEP) -> list[dict[str, Any]]:
    return await list_user_mcps(session["sub"])


@user_mcp_router.put("/my-mcps/{name}", response_model=MCPConnectionPublic)
async def api_save_my_mcp(
    name: str,
    update: MCPConnectionUpdate,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    try:
        return await save_user_mcp(session["sub"], name, update)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@user_mcp_router.delete("/my-mcps/{name}", status_code=204)
async def api_delete_my_mcp(name: str, session: dict[str, Any] = SESSION_DEP) -> None:
    await delete_user_mcp(session["sub"], name)


@user_mcp_router.post("/my-mcps/{name}/headers/reveal")
async def api_reveal_my_mcp_headers(
    name: str,
    session: dict[str, Any] = SESSION_DEP,
) -> JSONResponse:
    return _reveal_mcp_headers(await get_user_mcp(session["sub"], name))


@user_mcp_router.post("/my-mcps/{name}/discover", response_model=list[MCPToolDescription])
async def api_discover_my_mcp(
    name: str,
    update: MCPConnectionUpdate | None = None,
    session: dict[str, Any] = SESSION_DEP,
) -> list[dict[str, str]]:
    try:
        return await discover_user_mcp(session["sub"], name, update)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


router = APIRouter()
router.include_router(workspace_mcp_router)
router.include_router(user_mcp_router)
