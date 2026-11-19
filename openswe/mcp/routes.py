"""Dashboard API for LangSmith Managed Tools."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import ADMIN_DEP, SESSION_DEP
from openswe.dashboard.oauth import frontend_base_url
from openswe.mcp.cards import ConnectCard, connect_service
from openswe.mcp.managed import (
    BotKitViewer,
    LangSmithNotConnected,
    ManagedServer,
    ManagedToolsError,
    NewServer,
    PersonalTools,
    managed_tools_configured,
)
from openswe.utils.dashboard_links import dashboard_thread_url

logger = logging.getLogger(__name__)


class ManagedToolsView(BaseModel):
    """The caller's own LMT services: those they connected, and those they could."""

    configured: bool
    langsmith_connected: bool = False
    servers: list[ManagedServer] = []


class BotToolsView(BaseModel):
    """The services in Robin's bot kit, and whether the bot's LMT profile can use each."""

    configured: bool
    langsmith_connected: bool = False
    """Whether the admin's own LangSmith sign-in, which LMT requires to read a kit, works."""
    servers: list[ManagedServer] = []


class ManagedConnectResponse(BaseModel):
    connected: bool
    url: str | None = None


router = APIRouter()


def _managed_error(exc: ManagedToolsError) -> HTTPException:
    return HTTPException(409 if isinstance(exc, LangSmithNotConnected) else 502, str(exc))


@router.get("/my-managed-tools", response_model=ManagedToolsView)
async def api_my_managed_tools(session: dict[str, Any] = SESSION_DEP) -> ManagedToolsView:
    if not managed_tools_configured():
        return ManagedToolsView(configured=False)
    try:
        # A page a person looks at asks LangSmith now, not the runs' cached copy.
        servers = await PersonalTools(session["sub"]).kit_servers(fresh=True)
    except LangSmithNotConnected:
        return ManagedToolsView(configured=True)
    except ManagedToolsError as exc:
        raise _managed_error(exc) from None
    return ManagedToolsView(
        configured=True,
        langsmith_connected=True,
        servers=[server for server in servers if server.enabled],
    )


@router.get("/managed-tools/bot", response_model=BotToolsView)
async def api_bot_managed_tools(session: dict[str, Any] = ADMIN_DEP) -> BotToolsView:
    """What the bot kit offers runs outside private threads; credentials are managed in
    LangSmith, so this only reports them."""
    viewer = BotKitViewer(session["sub"])
    if not viewer.configured():
        return BotToolsView(configured=False)
    try:
        servers = await viewer.kit_servers(fresh=True)
    except LangSmithNotConnected:
        return BotToolsView(configured=True)
    except ManagedToolsError as exc:
        raise _managed_error(exc) from None
    return BotToolsView(
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


@router.post("/my-managed-tools/connect/{server}", response_model=ManagedConnectResponse)
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


@router.delete("/my-managed-tools/services/{server}", status_code=204)
@audit_endpoint
async def api_disconnect_managed_tool(server: str, session: dict[str, Any] = SESSION_DEP) -> None:
    """Remove the caller's own LangSmith connection to one service."""
    try:
        await PersonalTools(session["sub"]).disconnect(server)
    except ManagedToolsError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/my-managed-tools/servers", response_model=ManagedServer, status_code=201)
@audit_endpoint
async def api_add_managed_server(
    new: NewServer, session: dict[str, Any] = SESSION_DEP
) -> ManagedServer:
    """Register an MCP server in LangSmith that only the caller sees and uses."""
    try:
        return await PersonalTools(session["sub"]).add_server(new)
    except ManagedToolsError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("/my-managed-tools/proposed-server", response_model=ManagedServer | None)
async def api_get_proposed_managed_server(
    thread_id: str, card: str, session: dict[str, Any] = SESSION_DEP
) -> ManagedServer | None:
    """The server a thread's card added once its owner confirmed it; null while unconfirmed."""
    try:
        return await (await _card(thread_id, card, session["sub"])).added()
    except ManagedToolsError as exc:
        raise _managed_error(exc) from None


@router.post("/my-managed-tools/proposed-server", response_model=ManagedServer, status_code=201)
@audit_endpoint
async def api_add_proposed_managed_server(
    thread_id: str, card: str, session: dict[str, Any] = SESSION_DEP
) -> ManagedServer:
    """Confirm the server a thread's card proposed. What is registered comes from the
    stored card, so the request chooses no name or URL."""
    try:
        return await (await _card(thread_id, card, session["sub"])).add_proposed()
    except ManagedToolsError as exc:
        raise HTTPException(400, str(exc)) from None


@router.delete("/my-managed-tools/servers/{server}", status_code=204)
@audit_endpoint
async def api_remove_managed_server(server: str, session: dict[str, Any] = SESSION_DEP) -> None:
    """Delete a server the caller registered for themselves."""
    try:
        await PersonalTools(session["sub"]).remove_server(server)
    except ManagedToolsError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("/my-managed-tools/connect/{server}")
async def connect_managed_tool_from_card(
    server: str,
    thread_id: str | None = None,
    card: str | None = None,
    session: dict[str, Any] = SESSION_DEP,
) -> RedirectResponse:
    """A Connect link, from a thread's card (on the web or in Slack) or from Settings: mint
    the consent link here, for the signed-in person only, and send them on to it. A plain
    link works in every browser, unlike a script steering a tab it opened."""
    try:
        if thread_id and card:
            offered = await _card(thread_id, card, session["sub"])
            link = await offered.connect(server)
        else:
            offered = None
            link = await connect_service(session["sub"], server)
    except ManagedToolsError as exc:
        raise HTTPException(400, str(exc)) from None
    # Built from stored state, so no request value chooses where this redirects.
    if link is not None:
        target = link.url
    elif offered is not None:
        target = dashboard_thread_url(offered.thread_id) or frontend_base_url()
    else:
        target = frontend_base_url() + "/my-settings/connections"
    return RedirectResponse(target, status_code=302)
