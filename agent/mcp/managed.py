"""Personal MCP servers from LangSmith Managed Tools (LMT), connected per user.

Open SWE calls LMT as the person, with the access token from their Sign in with
LangSmith grant, so they see the same server connections they made anywhere else in
LangSmith. LMT owns each provider grant and proxies MCP calls; provider tokens,
consent links and the LangSmith token never reach the agent, a tool argument or the
sandbox.
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Literal
from uuid import UUID, uuid4

import httpx
from langchain_mcp_adapters.sessions import StreamableHttpConnection, create_session
from pydantic import BaseModel, Field, field_validator

from agent.config import ENV
from agent.credential_scope import private_credential_login
from agent.dashboard.langsmith_oauth import (
    langsmith_access_token,
    langsmith_issuer,
    langsmith_oauth_configured,
)
from agent.mcp.models import MCPConnection
from agent.mcp.runtime import MCPSource
from agent.mcp.transport import mcp_http_client
from agent.store import TypedStore, now_iso
from mcp import ClientSession
from mcp.types import PaginatedRequestParams

logger = logging.getLogger(__name__)

USER_MANAGED_MCPS_NAMESPACE = ["user_managed_mcps"]
SERVERS_PATH = "/v1/managed-tools/servers"
_TIMEOUT_SECONDS = 30
_MAX_CATALOG_PAGES = 5

type ServerKind = Literal["oauth", "secret", "none"]


class ManagedToolsError(ValueError):
    """A failure safe to show the person who asked."""


class ManagedConnectRequired(ManagedToolsError):
    """LMT has no usable grant for this person; ``url`` starts consent when present."""

    def __init__(self, url: str | None) -> None:
        super().__init__("Connect this server before using it")
        self.url = url


class LangSmithNotConnected(ManagedToolsError):
    def __init__(self) -> None:
        super().__init__("Connect LangSmith in Settings first")


class ManagedServer(BaseModel):
    """One workspace-registered LMT server as the dashboard shows it."""

    id: str
    name: str
    upstream_url: str
    kind: ServerKind
    connected: bool


class ManagedSelection(BaseModel):
    """A server one user turned on for their private runs, with the tools they allowed."""

    server_id: str
    name: str
    upstream_url: str
    enabled: bool = True
    allowed_tools: list[str] = Field(default_factory=list)
    revision: str
    updated_at: str


class ManagedSelectionUpdate(BaseModel):
    enabled: bool = True
    allowed_tools: list[str] = Field(default_factory=list)

    @field_validator("allowed_tools")
    @classmethod
    def _tools(cls, value: list[str]) -> list[str]:
        if any(not name.strip() or len(name) > 128 for name in value):
            raise ValueError("Tool names must be non-empty and at most 128 characters")
        return list(dict.fromkeys(name.strip() for name in value))


def managed_tools_configured() -> bool:
    return ENV.LMT_TENANT_ID.is_set() and langsmith_oauth_configured()


def _base_url() -> str:
    return langsmith_issuer()


async def _headers(login: str) -> dict[str, str]:
    if not managed_tools_configured():
        raise ManagedToolsError("Managed tools are not configured on this Open SWE instance")
    token = await langsmith_access_token(login)
    if token is None:
        raise LangSmithNotConnected
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": ENV.LMT_TENANT_ID.get()}


def _server_id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        raise ManagedToolsError("Unknown managed server") from None


def _client() -> httpx.AsyncClient:
    return mcp_http_client(_base_url(), timeout=httpx.Timeout(_TIMEOUT_SECONDS))


def _failure(response: httpx.Response) -> ManagedToolsError:
    if response.status_code == 428:
        try:
            body = response.json()
        except ValueError:
            body = {}
        url = body.get("verification_url") if isinstance(body, dict) else None
        if not isinstance(url, str) or not url.startswith("https://"):
            url = None
        return ManagedConnectRequired(url)
    if response.status_code in {401, 403}:
        return ManagedToolsError("Managed tools refused this request; check access to the server")
    if response.status_code == 404:
        return ManagedToolsError("The managed server is no longer available")
    return ManagedToolsError(f"Managed tools failed (HTTP {response.status_code}); retry later")


def _kind(connection: object) -> ServerKind:
    kind = connection.get("type") if isinstance(connection, dict) else None
    return kind if kind in {"oauth", "secret"} else "none"


async def list_managed_servers(login: str) -> list[ManagedServer]:
    """The workspace's registered servers and this person's connection to each.

    LangSmith built-ins are left out; ones with LangSmith-held keys bill the workspace.
    """
    servers: list[ManagedServer] = []
    headers = await _headers(login)
    async with _client() as client:
        cursor: str | None = None
        for _ in range(_MAX_CATALOG_PAGES):
            params = {"page_size": "100", **({"cursor": cursor} if cursor else {})}
            response = await client.get(_base_url() + SERVERS_PATH, headers=headers, params=params)
            if not response.is_success:
                raise _failure(response)
            payload = response.json()
            for item in payload.get("items", []):
                if not isinstance(item.get("id"), str):
                    continue
                servers.append(
                    ManagedServer(
                        id=item["id"],
                        name=str(item.get("name") or item["id"]),
                        upstream_url=str(item.get("upstream_url") or ""),
                        kind=_kind(item.get("connection")),
                        connected=item.get("connected") is True,
                    )
                )
            cursor = payload.get("next_cursor")
            if not cursor:
                break
    return sorted(servers, key=lambda server: server.name.lower())


async def get_managed_server(login: str, server_id: str) -> ManagedServer:
    server_id = _server_id(server_id)
    for server in await list_managed_servers(login):
        if server.id == server_id:
            return server
    raise ManagedToolsError("Unknown managed server")


def _mcp_url(server_id: str) -> str:
    return f"{_base_url()}{SERVERS_PATH}/{_server_id(server_id)}/mcp"


def _session_connection(headers: dict[str, str], server_id: str) -> StreamableHttpConnection:
    async def check(response: httpx.Response) -> None:
        if response.status_code >= 300:
            await response.aread()
            raise _failure(response)

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        client = mcp_http_client(_mcp_url(server_id), headers, timeout, auth)
        client.event_hooks["response"].append(check)
        return client

    return {
        "transport": "streamable_http",
        "url": _mcp_url(server_id),
        "headers": headers,
        "timeout": _TIMEOUT_SECONDS,
        "sse_read_timeout": _TIMEOUT_SECONDS,
        "httpx_client_factory": factory,
    }


def _wrapped[E: BaseException](error: BaseException, kind: type[E]) -> E | None:
    """MCP transports nest HTTP failures inside exception groups."""
    if isinstance(error, kind):
        return error
    if isinstance(error, BaseExceptionGroup):
        for child in error.exceptions:
            if (found := _wrapped(child, kind)) is not None:
                return found
    return None


async def _with_session[T](
    login: str, server_id: str, use: Callable[[ClientSession], Awaitable[T]]
) -> T:
    headers = await _headers(login)
    try:
        async with asyncio.timeout(_TIMEOUT_SECONDS):
            async with create_session(_session_connection(headers, server_id)) as session:
                await session.initialize()
                return await use(session)
    except Exception as exc:
        if (found := _wrapped(exc, ManagedToolsError)) is not None:
            raise found from None
        raise ManagedToolsError("Could not reach the managed server; retry later") from None


async def connect_url(login: str, server_id: str) -> str | None:
    """The consent URL LMT issues for this person, or None when already connected."""
    server = await get_managed_server(login, server_id)
    if server.kind == "secret":
        if server.connected:
            return None
        raise ManagedToolsError("Set this server's API key in LangSmith, then refresh")

    async def ping(_session: ClientSession) -> None:
        return None

    try:
        await _with_session(login, server.id, ping)
    except ManagedConnectRequired as required:
        if required.url is None:
            raise ManagedToolsError("Managed tools did not return a consent link") from None
        return required.url
    return None


async def discover_managed_tools(login: str, server_id: str) -> list[dict[str, str]]:
    """List the server's tools as this person sees them; never call any."""

    async def tools(session: ClientSession) -> list[dict[str, str]]:
        found: list[dict[str, str]] = []
        cursor: str | None = None
        while True:
            page = await session.list_tools(
                params=PaginatedRequestParams(cursor=cursor) if cursor else None
            )
            found.extend(
                {"name": tool.name, "description": tool.description or ""} for tool in page.tools
            )
            if not page.nextCursor or page.nextCursor == cursor:
                return found
            cursor = page.nextCursor

    return await _with_session(login, _server_id(server_id), tools)


async def disconnect_managed_server(login: str, server_id: str) -> None:
    headers = await _headers(login)
    async with _client() as client:
        response = await client.delete(
            f"{_base_url()}{SERVERS_PATH}/{_server_id(server_id)}/credential",
            headers=headers,
        )
    if response.status_code not in {204, 409} and not response.is_success:
        raise _failure(response)


def _store(login: str) -> TypedStore[ManagedSelection]:
    return TypedStore([*USER_MANAGED_MCPS_NAMESPACE, login.strip().lower()], ManagedSelection)


async def list_managed_selections(login: str) -> list[ManagedSelection]:
    return sorted(await _store(login).search_all(), key=lambda record: record.name.lower())


async def save_managed_selection(
    login: str, server_id: str, update: ManagedSelectionUpdate
) -> ManagedSelection:
    server = await get_managed_server(login, server_id)
    record = ManagedSelection(
        server_id=server.id,
        name=server.name,
        upstream_url=server.upstream_url,
        enabled=update.enabled,
        allowed_tools=update.allowed_tools,
        revision=uuid4().hex,
        updated_at=now_iso(),
    )
    await _store(login).put(server.id, record)
    return record


async def delete_managed_selection(login: str, server_id: str) -> None:
    await _store(login).delete(_server_id(server_id))


class _ManagedConnection(MCPConnection):
    """An LMT route carrying the caller's current token in memory, never stored."""

    headers: dict[str, str] = Field(default_factory=dict, repr=False, exclude=True)

    def connection_headers(self) -> dict[str, str]:
        return self.headers


def _connection_name(selection: ManagedSelection) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", selection.name.lower()).strip("-")[:20] or "server"
    return f"lmt-{slug}-{selection.server_id[:6]}"


def _as_connection(headers: dict[str, str], selection: ManagedSelection) -> MCPConnection:
    return _ManagedConnection(
        name=_connection_name(selection),
        url=_mcp_url(selection.server_id),
        enabled=selection.enabled,
        allowed_tools=selection.allowed_tools,
        revision=selection.revision,
        updated_at=selection.updated_at,
        headers=headers,
    )


async def _connections(login: str) -> list[MCPConnection]:
    """Resolved on every lookup, so each tool call carries a freshly refreshed token."""
    selections = await list_managed_selections(login) if managed_tools_configured() else []
    if not selections:
        return []
    try:
        headers = await _headers(login)
    except LangSmithNotConnected:
        logger.info("Managed tools skipped; LangSmith not connected", extra={"login": login})
        return []
    return [_as_connection(headers, selection) for selection in selections]


async def _connection(login: str, name: str) -> MCPConnection | None:
    return next((record for record in await _connections(login) if record.name == name), None)


def managed_mcp_source(login: str) -> MCPSource:
    """The private-thread owner's LMT servers, as an MCP source after their own MCPs."""
    login = login.strip().lower()

    async def authorize() -> None:
        owner = await private_credential_login()
        if owner is None or owner.strip().lower() != login:
            raise RuntimeError("Personal managed tools require the private thread owner")

    return MCPSource(
        namespace=(*USER_MANAGED_MCPS_NAMESPACE, login),
        list_connections=partial(_connections, login),
        get_connection=partial(_connection, login),
        authorize=authorize,
    )
