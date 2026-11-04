"""Dashboard API for workspace and per-user MCP connections, and managed tools."""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import ADMIN_DEP, SESSION_DEP
from openswe.dashboard.oauth import frontend_base_url
from openswe.mcp.cards import ConnectCard, connect_bot_service, connect_service
from openswe.mcp.managed import (
    BotStatus,
    BotTools,
    Kit,
    LangSmithNotConnected,
    ManagedServer,
    ManagedToolsError,
    NewServer,
    PersonalTools,
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
from openswe.utils.dashboard_links import dashboard_thread_url
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


workspace_mcp_router = APIRouter(route_class=MCPRoute)


@workspace_mcp_router.get("/workspaces/{workspace}/mcps", response_model=list[MCPConnectionPublic])
async def api_list_workspace_mcps(
    workspace: str, _admin: dict[str, Any] = ADMIN_DEP
) -> list[dict[str, Any]]:
    return await list_workspace_mcps(_normalized_workspace(workspace))


@workspace_mcp_router.put("/workspaces/{workspace}/mcps/{name}", response_model=MCPConnectionPublic)
@audit_endpoint
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
@audit_endpoint
async def api_delete_workspace_mcp(
    workspace: str, name: str, _admin: dict[str, Any] = ADMIN_DEP
) -> None:
    await delete_workspace_mcp(_normalized_workspace(workspace), name)


@workspace_mcp_router.post("/workspaces/{workspace}/mcps/{name}/headers/reveal")
@audit_endpoint
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
@audit_endpoint
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
@audit_endpoint
async def api_delete_my_mcp(name: str, session: dict[str, Any] = SESSION_DEP) -> None:
    await delete_user_mcp(session["sub"], name)


@user_mcp_router.post("/my-mcps/{name}/headers/reveal")
@audit_endpoint
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
    """The caller's own LMT services: those they connected, and those they could."""

    configured: bool
    langsmith_connected: bool = False
    servers: list[ManagedServer] = []


class ManagedConnectResponse(BaseModel):
    connected: bool
    url: str | None = None


class BotToolsView(BaseModel):
    """A workspace bot's kit and profile, and every server with whether the bot has it."""

    status: BotStatus | None = None
    error: str | None = None


class NewBotServer(BaseModel):
    server: ManagedServer
    url: str | None = None
    """Where the admin connects it for the bot; None when it needed no connection."""


class BotServerTools(BaseModel):
    """One server's tools the bot can list, and those its kit allows."""

    available: list[str]
    allowed: list[str]


class BotToolsUpdate(BaseModel):
    tools: list[str]


managed_mcp_router = APIRouter(route_class=MCPRoute)


def _managed_error(exc: ManagedToolsError) -> HTTPException:
    return HTTPException(409 if isinstance(exc, LangSmithNotConnected) else 502, str(exc))


@managed_mcp_router.get("/managed-tools/kits", response_model=list[Kit])
async def api_list_managed_tools_kits(admin: dict[str, Any] = ADMIN_DEP) -> list[Kit]:
    """Tool kits an admin can point a workspace bot at, read with their own LangSmith login."""
    try:
        return await PersonalTools(admin["sub"]).kits()
    except ManagedToolsError as exc:
        raise _managed_error(exc) from None


@managed_mcp_router.get("/workspaces/{workspace}/bot-tools", response_model=BotToolsView)
async def api_workspace_bot_tools(
    workspace: str, _admin: dict[str, Any] = ADMIN_DEP
) -> BotToolsView:
    slug = _normalized_workspace(workspace)
    if not managed_tools_configured():
        return BotToolsView()
    try:
        return BotToolsView(status=await (await BotTools.of(slug)).status())
    except ManagedToolsError as exc:
        logger.warning("Workspace bot status unavailable", extra={"workspace": slug}, exc_info=True)
        return BotToolsView(error=str(exc))


@managed_mcp_router.post(
    "/workspaces/{workspace}/bot-tools/connect/{server}", response_model=ManagedConnectResponse
)
@audit_endpoint
async def api_connect_workspace_bot_tool(
    workspace: str, server: str, admin: dict[str, Any] = ADMIN_DEP
) -> ManagedConnectResponse:
    """Give the workspace bot one existing server: a consent link where the admin connects
    it, after which its tools join the bot's kit."""
    bot = await BotTools.of(_normalized_workspace(workspace))
    try:
        link = await connect_bot_service(admin["sub"], bot, server)
    except ManagedToolsError as exc:
        raise HTTPException(400, str(exc)) from None
    return ManagedConnectResponse(connected=link is None, url=link.url if link else None)


@managed_mcp_router.post(
    "/workspaces/{workspace}/bot-tools/servers", response_model=NewBotServer, status_code=201
)
@audit_endpoint
async def api_add_workspace_bot_server(
    workspace: str, body: NewServer, admin: dict[str, Any] = ADMIN_DEP
) -> NewBotServer:
    """Register a server in LMT, then give it to the workspace bot."""
    bot = await BotTools.of(_normalized_workspace(workspace))
    try:
        server = await PersonalTools(admin["sub"]).create_server(body)
        link = await connect_bot_service(admin["sub"], bot, server.id)
    except ManagedToolsError as exc:
        raise HTTPException(400, str(exc)) from None
    return NewBotServer(server=server, url=link.url if link else None)


@managed_mcp_router.get(
    "/workspaces/{workspace}/bot-tools/services/{server}/tools", response_model=BotServerTools
)
async def api_workspace_bot_server_tools(
    workspace: str, server: str, _admin: dict[str, Any] = ADMIN_DEP
) -> BotServerTools:
    bot = await BotTools.of(_normalized_workspace(workspace))
    try:
        available, kit_tools = await asyncio.gather(bot.server_tools(server), bot.kit_tools())
    except ManagedToolsError as exc:
        raise HTTPException(400, str(exc)) from None
    return BotServerTools(available=available, allowed=sorted(kit_tools.get(server, ())))


@managed_mcp_router.put(
    "/workspaces/{workspace}/bot-tools/services/{server}/tools", response_model=BotServerTools
)
@audit_endpoint
async def api_set_workspace_bot_server_tools(
    workspace: str, server: str, body: BotToolsUpdate, admin: dict[str, Any] = ADMIN_DEP
) -> BotServerTools:
    """Set which of one server's tools the bot's kit allows; none takes it out of the kit."""
    bot = await BotTools.of(_normalized_workspace(workspace))
    tools = PersonalTools(admin["sub"])
    try:
        available = await bot.server_tools(server)
        if unknown := set(body.tools) - set(available):
            raise ManagedToolsError(f"The server has no tool named {sorted(unknown)[0]}")
        bot = await tools.set_bot_tools(bot, server, body.tools)
        allowed = sorted((await bot.kit_tools()).get(server, ()))
    except ManagedToolsError as exc:
        raise HTTPException(400, str(exc)) from None
    return BotServerTools(available=available, allowed=allowed)


@managed_mcp_router.delete("/workspaces/{workspace}/bot-tools/services/{server}", status_code=204)
@audit_endpoint
async def api_remove_workspace_bot_tool(
    workspace: str, server: str, admin: dict[str, Any] = ADMIN_DEP
) -> None:
    """Take a server out of the bot's kit and delete the bot's credential for it."""
    bot = await BotTools.of(_normalized_workspace(workspace))
    try:
        await PersonalTools(admin["sub"]).remove_from_bot(bot, server)
    except ManagedToolsError as exc:
        raise HTTPException(400, str(exc)) from None


@managed_mcp_router.get("/my-managed-tools", response_model=ManagedToolsView)
async def api_my_managed_tools(session: dict[str, Any] = SESSION_DEP) -> ManagedToolsView:
    if not managed_tools_configured():
        return ManagedToolsView(configured=False)
    try:
        servers = await PersonalTools(session["sub"]).servers()
    except LangSmithNotConnected:
        return ManagedToolsView(configured=True)
    except ManagedToolsError as exc:
        raise _managed_error(exc) from None
    return ManagedToolsView(
        configured=True,
        langsmith_connected=True,
        servers=[server for server in servers if server.enabled],
    )


async def _card(thread_id: str, card_id: str, login: str) -> ConnectCard:
    """The connect card the agent offered this person in their thread."""
    card = await ConnectCard.load(thread_id, card_id, login)
    if card is None:
        raise HTTPException(404, "Unknown connect card")
    return card


@managed_mcp_router.post(
    "/my-managed-tools/connect/{server}", response_model=ManagedConnectResponse
)
@audit_endpoint
async def api_connect_managed_tool(
    server: str,
    thread_id: str | None = None,
    card: str | None = None,
    session: dict[str, Any] = SESSION_DEP,
) -> ManagedConnectResponse:
    """A consent link for one service; from a thread's card, the thread resumes once connected."""
    try:
        if thread_id and card:
            link = await (await _card(thread_id, card, session["sub"])).connect(server)
        else:
            link = await connect_service(session["sub"], server)
    except ManagedToolsError as exc:
        raise HTTPException(400, str(exc)) from None
    return ManagedConnectResponse(connected=link is None, url=link.url if link else None)


@managed_mcp_router.delete("/my-managed-tools/services/{server}", status_code=204)
@audit_endpoint
async def api_disconnect_managed_tool(server: str, session: dict[str, Any] = SESSION_DEP) -> None:
    """Remove the caller's own LangSmith connection to one service."""
    try:
        await PersonalTools(session["sub"]).disconnect(server)
    except ManagedToolsError as exc:
        raise HTTPException(400, str(exc)) from None


@managed_mcp_router.get("/my-managed-tools/connect/{server}")
async def connect_managed_tool_from_card(
    server: str, thread_id: str, card: str, session: dict[str, Any] = SESSION_DEP
) -> RedirectResponse:
    """A Slack card button: mint the consent link here, for the signed-in owner only."""
    try:
        offered = await _card(thread_id, card, session["sub"])
        link = await offered.connect(server)
    except ManagedToolsError as exc:
        raise HTTPException(400, str(exc)) from None
    # Built from the stored card, so no request value chooses where this redirects.
    target = link.url if link else dashboard_thread_url(offered.thread_id) or frontend_base_url()
    return RedirectResponse(target, status_code=302)


router = APIRouter()
router.include_router(workspace_mcp_router)
router.include_router(user_mcp_router)
router.include_router(managed_mcp_router)
