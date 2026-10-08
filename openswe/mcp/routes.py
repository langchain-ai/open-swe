"""Dashboard API for instance-wide, workspace, and per-user MCP connections."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from openswe.dashboard.deps import ADMIN_DEP, SESSION_DEP
from openswe.mcp.instance import (
    delete_instance_mcp,
    discover_instance_mcp,
    get_instance_mcp,
    list_instance_mcps,
    save_instance_mcp,
)
from openswe.mcp.managed import (
    Gateway,
    GatewayStatus,
    LangSmithNotConnected,
    ManagedToolsError,
    connect_url,
    gateway_id,
    gateway_status,
    list_gateways,
    managed_tools_configured,
)
from openswe.mcp.models import (
    MCPConnection,
    MCPConnectionPublic,
    MCPConnectionUpdate,
    MCPToolDescription,
)
from openswe.mcp.user import (
    delete_user_mcp,
    discover_user_mcp,
    get_user_mcp,
    list_user_mcps,
    save_user_mcp,
)
from openswe.mcp.workspace import (
    MCPRoute,
    delete_workspace_mcp,
    get_workspace_mcp,
    list_workspace_mcps,
    save_workspace_mcp,
)
from openswe.tool_loaders.workspace_mcp import discover_workspace_mcp
from openswe.workspaces.store import slugify

logger = logging.getLogger(__name__)


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


instance_mcp_router = APIRouter(route_class=MCPRoute)


@instance_mcp_router.get("/mcps", response_model=list[MCPConnectionPublic])
async def api_list_instance_mcps(_admin: dict[str, Any] = ADMIN_DEP) -> list[dict[str, Any]]:
    return await list_instance_mcps()


@instance_mcp_router.put("/mcps/{name}", response_model=MCPConnectionPublic)
async def api_save_instance_mcp(
    name: str, update: MCPConnectionUpdate, _admin: dict[str, Any] = ADMIN_DEP
) -> dict[str, Any]:
    try:
        return await save_instance_mcp(name, update)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@instance_mcp_router.delete("/mcps/{name}", status_code=204)
async def api_delete_instance_mcp(name: str, _admin: dict[str, Any] = ADMIN_DEP) -> None:
    await delete_instance_mcp(name)


@instance_mcp_router.post("/mcps/{name}/headers/reveal")
async def api_reveal_instance_mcp_headers(
    name: str, _admin: dict[str, Any] = ADMIN_DEP
) -> JSONResponse:
    return _reveal_mcp_headers(await get_instance_mcp(name))


@instance_mcp_router.post("/mcps/{name}/discover", response_model=list[MCPToolDescription])
async def api_discover_instance_mcp(
    name: str,
    update: MCPConnectionUpdate | None = None,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> list[dict[str, str]]:
    try:
        return await discover_instance_mcp(name, update)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


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


class ManagedToolsView(BaseModel):
    """The managed tools gateways the caller's workspaces use, and what each still needs."""

    configured: bool
    langsmith_connected: bool = False
    gateways: list[GatewayStatus]


class ManagedConnectResponse(BaseModel):
    connected: bool
    url: str | None = None


async def _gateway_workspaces() -> dict[str, list[str]]:
    """Each gateway an admin picked, with the workspaces that load it."""
    from openswe.dashboard.workspace_settings import get_workspace_settings
    from openswe.workspaces.store import list_workspace_options

    by_gateway: dict[str, list[str]] = {}
    for option in await list_workspace_options():
        slug = str(option["slug"])
        if gateway := (await get_workspace_settings(slug)).managed_tools_gateway_id:
            by_gateway.setdefault(gateway, []).append(slug)
    return by_gateway


managed_mcp_router = APIRouter(route_class=MCPRoute)


@managed_mcp_router.get("/managed-tools/gateways", response_model=list[Gateway])
async def api_list_managed_tools_gateways(admin: dict[str, Any] = ADMIN_DEP) -> list[Gateway]:
    """Gateways an admin can pick for a workspace, read with the admin's own LangSmith login."""
    try:
        return await list_gateways(admin["sub"])
    except LangSmithNotConnected as exc:
        raise HTTPException(409, str(exc)) from None
    except ManagedToolsError as exc:
        raise HTTPException(502, str(exc)) from None


@managed_mcp_router.get("/my-managed-tools", response_model=ManagedToolsView)
async def api_my_managed_tools(session: dict[str, Any] = SESSION_DEP) -> ManagedToolsView:
    if not managed_tools_configured():
        return ManagedToolsView(configured=False, gateways=[])
    gateways = await _gateway_workspaces()
    statuses: list[GatewayStatus] = []
    for gateway, workspaces in gateways.items():
        try:
            statuses.append(await gateway_status(session["sub"], gateway, workspaces))
        except LangSmithNotConnected:
            return ManagedToolsView(configured=True, gateways=[])
        except ManagedToolsError:
            # One deleted or unreachable gateway must not hide the others.
            logger.warning(
                "Managed tools gateway status unavailable",
                extra={"gateway": gateway, "workspaces": workspaces},
                exc_info=True,
            )
    return ManagedToolsView(configured=True, langsmith_connected=True, gateways=statuses)


@managed_mcp_router.post(
    "/my-managed-tools/{gateway}/connect/{slug}", response_model=ManagedConnectResponse
)
async def api_connect_managed_tool(
    gateway: str, slug: str, session: dict[str, Any] = SESSION_DEP
) -> ManagedConnectResponse:
    try:
        if gateway_id(gateway) not in await _gateway_workspaces():
            raise HTTPException(404, "No workspace uses this gateway")
        url = await connect_url(session["sub"], gateway, slug)
    except ManagedToolsError as exc:
        raise HTTPException(400, str(exc)) from None
    return ManagedConnectResponse(connected=url is None, url=url)


router = APIRouter()
router.include_router(instance_mcp_router)
router.include_router(workspace_mcp_router)
router.include_router(user_mcp_router)
router.include_router(managed_mcp_router)
