"""LangSmith Managed Tools (LMT) gateways, used by each person with their own grants.

An instance admin picks one LMT gateway (a curated set of tools across MCP servers,
served from one MCP URL). Private threads in every workspace load the gateway with the
owner's own LangSmith token, so every tool call runs with the provider connections
that person made in LangSmith. LMT owns those provider grants and proxies the calls;
provider tokens, consent links and the LangSmith token never reach the agent, a tool
argument or the sandbox.
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Literal
from urllib.parse import quote
from uuid import UUID

import httpx2
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from pydantic import BaseModel, Field

from openswe.config import ENV
from openswe.credential_scope import private_credential_login
from openswe.dashboard.langsmith_oauth import (
    LangSmithOAuthError,
    langsmith_access_token,
    langsmith_issuer,
    langsmith_oauth_configured,
)
from openswe.mcp.models import MCPConnection
from openswe.mcp.runtime import MCPSource, refresh_mcp_catalog
from openswe.mcp.transport import mcp_http_client

logger = logging.getLogger(__name__)

KITS_PATH = "/v1/managed-tools/kits"
SERVERS_PATH = "/v1/managed-tools/servers"
CONSENT_SESSIONS_PATH = "/v1/agent-auth/oauth-authorization-sessions"
CONNECTION_NAME = "lmt"
_MISSING_CREDENTIALS_META = "com.langchain.langsmith/missingCredentials"
_LANGSMITH_APP_SLUG_PREFIX = "langsmith-"
_TIMEOUT_SECONDS = 30
_MAX_PAGES = 5
# LangSmith holds each status request open this long while consent is pending.
_CONSENT_WAIT_SECONDS = 25
# Consent links expire after ten minutes; stop waiting shortly after.
_CONSENT_DEADLINE_SECONDS = 11 * 60
_CONSENT_POLL_FLOOR_SECONDS = 1

type CredentialKind = Literal["oauth", "secret"]
type ConsentOutcome = Literal["completed", "failed", "expired"]


class ManagedToolsError(ValueError):
    """A failure safe to show the person who asked."""


class LangSmithNotConnected(ManagedToolsError):
    def __init__(self) -> None:
        super().__init__("Connect LangSmith in Settings first")


class MissingCredential(BaseModel):
    """A service in the gateway this person has not connected yet."""

    slug: str
    """Which service, for connect links and cards: LMT's credential slug, made per service
    where several share it (see ``_service_slug``)."""
    display_name: str
    kind: CredentialKind


class ConsentLink(BaseModel):
    """LMT's single-use consent link for one service; whoever completes it connects the
    service to the token owner's account, so it goes only to that person."""

    url: str
    auth_id: str | None = None


class GatewayCredentialsRequired(ManagedToolsError):
    """LMT's 428: a gateway that offers no tools until every listed service is connected."""

    def __init__(self, missing: list[MissingCredential], links: dict[str, ConsentLink]) -> None:
        super().__init__("Connect every service in this tool kit before using its tools")
        self.missing = missing
        self.links = links


class ConnectedService(BaseModel):
    """A service in the gateway this person has connected."""

    id: str
    """LMT's server ID or built-in key, which disconnecting names."""
    name: str
    tool_count: int
    disconnectable: bool = False
    """This person has a stored credential for it; public services have none to remove."""


class Gateway(BaseModel):
    id: str
    name: str
    tool_count: int


class GatewayStatus(BaseModel):
    """What one person sees for a gateway: every service connected, or what is left."""

    gateway: Gateway
    ready: bool
    tool_count: int | None = None
    missing: list[MissingCredential] = Field(default_factory=list)
    connected: list[ConnectedService] = Field(default_factory=list)
    """Filled only when asked for, since naming them costs another LangSmith read."""
    all_required: bool = False
    """LangSmith withholds every tool until all of ``missing`` are connected. Otherwise
    the connected services' tools load and each missing one can be connected alone."""

    def missing_for(self, services: Sequence[str]) -> list[MissingCredential]:
        """The unconnected services among ``services`` (slugs or names, as people say them).

        Every unconnected service when none is named, or when the gateway needs them all.
        """
        if self.all_required or not services:
            return self.missing
        keys = {key for service in services if (key := _match_key(service))}
        return [
            item
            for item in self.missing
            if item.slug in services or any(key in _match_key(item.display_name) for key in keys)
        ]


def _match_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


@dataclass(frozen=True)
class _Listing:
    """One tools/list read: how many tools it offered, and what is still unconnected."""

    tool_count: int | None
    missing: list[MissingCredential]
    links: dict[str, ConsentLink]
    all_required: bool
    tool_names: frozenset[str] = frozenset()


def managed_tools_configured() -> bool:
    return langsmith_oauth_configured()


async def instance_gateway() -> str | None:
    """The gateway an instance admin picked; workspaces cannot override it."""
    from openswe.dashboard.workspace_settings_cache import cached_instance_settings

    return (await cached_instance_settings()).managed_tools_gateway_id


def gateway_id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        raise ManagedToolsError("Unknown tool kit") from None


async def _headers(login: str) -> dict[str, str]:
    if not managed_tools_configured():
        raise ManagedToolsError("Managed tools are not configured on this Open SWE instance")
    token = await langsmith_access_token(login)
    if token is None:
        raise LangSmithNotConnected
    headers = {"Authorization": f"Bearer {token}"}
    # Without a pin, LangSmith uses the workspace the person chose when signing in,
    # which their token carries.
    if tenant := ENV.LMT_TENANT_ID.optional():
        headers["X-Tenant-Id"] = tenant
    return headers


def _client() -> httpx2.AsyncClient:
    return mcp_http_client(langsmith_issuer(), timeout=httpx2.Timeout(_TIMEOUT_SECONDS))


def _credentials(entries: object) -> tuple[list[MissingCredential], dict[str, ConsentLink]]:
    missing: list[MissingCredential] = []
    links: dict[str, ConsentLink] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("slug"), str):
            continue
        kind: CredentialKind = "secret" if entry.get("kind") == "secret" else "oauth"
        display_name = str(entry.get("display_name") or entry["slug"])
        slug = _service_slug(entry["slug"], display_name)
        missing.append(MissingCredential(slug=slug, display_name=display_name, kind=kind))
        url = entry.get("verification_url")
        if kind == "oauth" and isinstance(url, str) and url.startswith("https://"):
            auth_id = entry.get("auth_id")
            links[slug] = ConsentLink(
                url=url, auth_id=auth_id if isinstance(auth_id, str) else None
            )
    return missing, links


def _service_slug(slug: str, display_name: str) -> str:
    """One key per service, even where services share an LMT connection.

    LMT keeps one connection per LangSmith OAuth app (slug prefix ``langsmith-``, e.g.
    ``langsmith-google``) for every built-in on it, but lists each built-in apart with its
    own consent link asking only that built-in's scopes. Keyed by slug alone, all but one
    link would be dropped and every button would grant the same built-in.
    """
    if slug.startswith(_LANGSMITH_APP_SLUG_PREFIX):
        return f"{slug}:{_match_key(display_name)}"
    return slug


def _credentials_required(body: object) -> GatewayCredentialsRequired:
    return GatewayCredentialsRequired(
        *_credentials(body.get("credentials") if isinstance(body, dict) else None)
    )


def _failure(response: httpx2.Response) -> ManagedToolsError:
    if response.status_code == 428:
        try:
            body = response.json()
        except ValueError:
            logger.warning("Unreadable LMT credentials challenge")
            body = None
        return _credentials_required(body)
    if response.status_code in {401, 403}:
        return ManagedToolsError("LangSmith refused access to this tool kit")
    if response.status_code == 404:
        return ManagedToolsError("The tool kit no longer exists")
    return ManagedToolsError(f"Managed tools failed (HTTP {response.status_code}); retry later")


def _gateway(item: dict[str, object]) -> Gateway:
    tools = item.get("tools")
    count = item.get("tool_count")
    return Gateway(
        id=str(item["id"]),
        name=str(item.get("name") or item["id"]),
        tool_count=count
        if isinstance(count, int)
        else len(tools)
        if isinstance(tools, list)
        else 0,
    )


async def list_gateways(login: str) -> list[Gateway]:
    """The gateways in the LangSmith workspace, read as ``login`` (for the admin picker)."""
    headers = await _headers(login)
    gateways: list[Gateway] = []
    async with _client() as client:
        cursor: str | None = None
        for _ in range(_MAX_PAGES):
            params = {"page_size": "100", **({"cursor": cursor} if cursor else {})}
            response = await client.get(
                langsmith_issuer() + KITS_PATH, headers=headers, params=params
            )
            if not response.is_success:
                raise _failure(response)
            payload = response.json()
            gateways.extend(
                _gateway(item)
                for item in payload.get("items", [])
                if isinstance(item, dict) and item.get("id")
            )
            cursor = payload.get("next_cursor")
            if not cursor:
                break
    return sorted(gateways, key=lambda gateway: gateway.name.lower())


async def _gateway_detail(login: str, gateway: str) -> dict[str, object]:
    headers = await _headers(login)
    async with _client() as client:
        response = await client.get(
            f"{langsmith_issuer()}{KITS_PATH}/{gateway_id(gateway)}", headers=headers
        )
    if not response.is_success:
        raise _failure(response)
    return response.json()


@dataclass(frozen=True)
class _Server:
    """What LMT's server list says about one server: its name, credential slugs, and
    whether the caller has stored a credential for it."""

    name: str
    slugs: frozenset[str]
    has_credential: bool = False


def _server(item: dict[str, object]) -> _Server:
    # A missing credential names its server by one of these (smith-go connectionSlug).
    slugs = {f"mt-{item['id']}"}
    if isinstance(key := item.get("key"), str) and key:
        slugs |= {key, f"mt-builtin-{key}"}
    connection = item.get("connection")
    if isinstance(connection, dict) and isinstance(
        slug := connection.get("agent_auth_connection_slug"), str
    ):
        slugs.add(slug)
    return _Server(
        name=str(item["name"]),
        slugs=frozenset(slugs),
        has_credential=item.get("has_credential") is True,
    )


async def _servers(login: str) -> dict[str, _Server]:
    """Servers by id and key: every server a tool kit can hold.

    LMT lists only the built-ins it supports (public and API-key ones) and admits only
    those into tool kits, so OAuth built-ins are neither listed nor in a kit. Callers
    fall back to a name from the kit when this read fails.
    """
    headers = await _headers(login)
    servers: dict[str, _Server] = {}
    async with _client() as client:
        cursor: str | None = None
        for _ in range(_MAX_PAGES):
            params = {"page_size": "100", **({"cursor": cursor} if cursor else {})}
            response = await client.get(
                langsmith_issuer() + SERVERS_PATH, headers=headers, params=params
            )
            if not response.is_success:
                raise _failure(response)
            payload = response.json()
            for item in payload.get("items", []):
                if not isinstance(item, dict):
                    continue
                if not isinstance(item.get("name"), str) or not isinstance(item.get("id"), str):
                    continue
                server = _server(item)
                for key in ("id", "key"):
                    if isinstance(item.get(key), str):
                        servers[item[key]] = server
            cursor = payload.get("next_cursor")
            if not cursor:
                break
    return servers


@dataclass(frozen=True)
class _Source:
    """One server's tools in a gateway, by the names the gateway lists them under."""

    id: str
    fallback_name: str
    tool_names: list[str]


def _sources(detail: dict[str, object]) -> list[_Source]:
    """Group the gateway's tools by server, in the order servers first appear."""
    grouped: dict[str, _Source] = {}
    tools = detail.get("tools")
    for tool in tools if isinstance(tools, list) else []:
        if not isinstance(tool, dict) or not isinstance(tool.get("mcp_name"), str):
            continue
        source = tool.get("server_id") or tool.get("builtin_server_key")
        if not isinstance(source, str):
            continue
        if source not in grouped:
            grouped[source] = _Source(source, _fallback_name(tool), [])
        grouped[source].tool_names.append(tool["mcp_name"])
    return list(grouped.values())


def _fallback_name(tool: dict[str, object]) -> str:
    """The server part of a gateway tool name (``<server>_<tool>``), made readable."""
    if isinstance(key := tool.get("builtin_server_key"), str) and key:
        name = key
    else:
        name = str(tool["mcp_name"])
        upstream = tool.get("tool_name")
        if isinstance(upstream, str) and name.endswith("_" + upstream):
            name = name.removesuffix("_" + upstream)
    name = re.sub(r"[_-]+", " ", name).strip()
    return name[:1].upper() + name[1:]


def _connected_services(
    sources: list[_Source], servers: dict[str, _Server], listing: _Listing
) -> list[ConnectedService]:
    missing_slugs = {item.slug for item in listing.missing}
    missing_names = {_match_key(item.display_name) for item in listing.missing}
    connected: list[ConnectedService] = []
    for source in sources:
        server = servers.get(source.id)
        name = server.name if server else source.fallback_name
        slugs = (
            server.slugs
            if server
            else frozenset({source.id, f"mt-{source.id}", f"mt-builtin-{source.id}"})
        )
        if listing.all_required:
            # LangSmith listed nothing, so a service counts as connected when it is not missing:
            # matched by credential slug first, then by name.
            if slugs & missing_slugs or _match_key(name) in missing_names:
                continue
            count = len(source.tool_names)
        else:
            count = sum(tool in listing.tool_names for tool in source.tool_names)
            if not count:
                continue
        connected.append(
            ConnectedService(
                id=source.id,
                name=name,
                tool_count=count,
                disconnectable=server.has_credential if server else False,
            )
        )
    return connected


def _mcp_url(gateway: str) -> str:
    return f"{langsmith_issuer()}{KITS_PATH}/{gateway_id(gateway)}/mcp"


def _gateway_client(headers: dict[str, str], gateway: str) -> Client[StreamableHttpTransport]:
    async def check(response: httpx2.Response) -> None:
        if response.status_code >= 300:
            await response.aread()
            raise _failure(response)

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
        follow_redirects: bool = False,
    ) -> httpx2.AsyncClient:
        client = mcp_http_client(_mcp_url(gateway), headers, timeout, auth, follow_redirects)
        client.event_hooks["response"].append(check)
        return client

    transport = StreamableHttpTransport(
        _mcp_url(gateway), headers=headers, httpx_client_factory=factory
    )
    return Client(transport, timeout=_TIMEOUT_SECONDS)


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
    login: str, gateway: str, use: Callable[[Client[StreamableHttpTransport]], Awaitable[T]]
) -> T:
    headers = await _headers(login)
    try:
        async with asyncio.timeout(_TIMEOUT_SECONDS):
            async with _gateway_client(headers, gateway) as client:
                return await use(client)
    except Exception as exc:
        if (found := _wrapped(exc, ManagedToolsError)) is not None:
            raise found from None
        raise ManagedToolsError("Could not reach the tool kit; retry later") from None


async def _listing(login: str, gateway: str) -> _Listing:
    """Read the gateway's tools as ``login``, under either of LMT's connection policies.

    A gateway that requires every connection answers 428 until all are made. One that
    allows partial use lists the connected services' tools and names the rest in _meta.
    """
    try:
        result = await _with_session(login, gateway, lambda client: client.list_tools_mcp())
    except GatewayCredentialsRequired as required:
        return _Listing(None, required.missing, required.links, all_required=True)
    missing, links = _credentials((result.meta or {}).get(_MISSING_CREDENTIALS_META))
    return _Listing(
        len(result.tools),
        missing,
        links,
        all_required=False,
        tool_names=frozenset(tool.name for tool in result.tools),
    )


async def gateway_status(
    login: str, gateway: str, *, with_connected: bool = False
) -> GatewayStatus:
    """Whether ``login`` has connected every service in the gateway, or which are left.

    ``with_connected`` also names the services already connected, for settings pages.
    """

    async def servers() -> dict[str, _Server]:
        try:
            return await _servers(login)
        except ManagedToolsError:
            logger.warning("Managed tools server names unavailable", exc_info=True)
            return {}

    # Independent LangSmith reads; the tools/list fan-out dominates, so run them together.
    if with_connected:
        detail, listing, names = await asyncio.gather(
            _gateway_detail(login, gateway), _listing(login, gateway), servers()
        )
        connected = _connected_services(_sources(detail), names, listing)
    else:
        detail, listing = await asyncio.gather(
            _gateway_detail(login, gateway), _listing(login, gateway)
        )
        connected = []
    return GatewayStatus(
        gateway=_gateway(detail),
        ready=not listing.missing,
        tool_count=listing.tool_count,
        missing=listing.missing,
        connected=connected,
        all_required=listing.all_required,
    )


async def connect_link(login: str, gateway: str, slug: str) -> ConsentLink | None:
    """A fresh consent link for one service in the gateway; None when it is connected."""
    listing = await _listing(login, gateway)
    if any(item.slug == slug and item.kind == "secret" for item in listing.missing):
        raise ManagedToolsError("Set this service's API key in LangSmith, then refresh")
    if slug in listing.links:
        return listing.links[slug]
    if any(item.slug == slug for item in listing.missing):
        raise ManagedToolsError("LangSmith did not return a consent link; retry")
    return None


async def disconnect_service(login: str, gateway: str, service: str) -> None:
    """Delete ``login``'s own LangSmith credential for one service in the gateway.

    LangSmith keeps the server and everyone else's credentials; the person can connect again.
    """
    if service not in {source.id for source in _sources(await _gateway_detail(login, gateway))}:
        raise ManagedToolsError("This tool kit has no such service")
    headers = await _headers(login)
    async with _client() as client:
        response = await client.delete(
            f"{langsmith_issuer()}{SERVERS_PATH}/{quote(service, safe='')}/credential",
            headers=headers,
        )
    if response.status_code == 404:
        raise ManagedToolsError("This service no longer exists in LangSmith")
    if response.status_code == 409:
        raise ManagedToolsError("This service has no connection to remove")
    if not response.is_success:
        raise _failure(response)
    try:
        await refresh_tools(login, gateway)
    except Exception:
        # A gateway that now misses a service may refuse to list; its stale list expires.
        logger.warning(
            "Managed tools refresh after disconnect failed",
            extra={"gateway": gateway},
            exc_info=True,
        )


async def refresh_tools(login: str, gateway: str) -> None:
    """Re-read the gateway's cached tool list, so a newly connected service's tools load."""
    await refresh_mcp_catalog(managed_mcp_source(login, gateway), CONNECTION_NAME)


async def consent_outcome(login: str, auth_id: str) -> ConsentOutcome:
    """Wait for the person to finish (or abandon) one consent link."""
    headers = await _headers(login)
    url = f"{langsmith_issuer()}{CONSENT_SESSIONS_PATH}/{quote(auth_id, safe='')}"
    timeout = httpx2.Timeout(_TIMEOUT_SECONDS + _CONSENT_WAIT_SECONDS)
    async with asyncio.timeout(_CONSENT_DEADLINE_SECONDS):
        async with mcp_http_client(langsmith_issuer(), timeout=timeout) as client:
            while True:
                response = await client.get(
                    url, headers=headers, params={"wait_seconds": str(_CONSENT_WAIT_SECONDS)}
                )
                if not response.is_success:
                    raise ManagedToolsError(
                        f"Consent status unavailable (HTTP {response.status_code})"
                    )
                status = response.json().get("status")
                if status in ("completed", "failed", "expired"):
                    return status
                if status != "pending":
                    raise ManagedToolsError("LangSmith returned an unknown consent status")
                # Long polls return early sometimes; never hammer LangSmith with the owner's token.
                await asyncio.sleep(_CONSENT_POLL_FLOOR_SECONDS)


class _GatewayConnection(MCPConnection):
    """The gateway route, carrying the caller's current token in memory, never stored.

    Every tool the gateway lists is offered: the admin curates the gateway in LangSmith.
    """

    headers: dict[str, str] = Field(default_factory=dict, repr=False, exclude=True)

    def connection_headers(self) -> dict[str, str]:
        return self.headers

    def allows_tool(self, name: str) -> bool:
        return True

    @property
    def offers_tools(self) -> bool:
        return True


async def _connections(login: str, gateway: str) -> list[MCPConnection]:
    """Resolved on every lookup, so each tool call carries a freshly refreshed token."""
    if not managed_tools_configured():
        return []
    try:
        headers = await _headers(login)
    except LangSmithNotConnected:
        logger.info("Managed tools skipped; LangSmith not connected", extra={"login": login})
        return []
    except LangSmithOAuthError as exc:
        # Raising would make the runtime drop every tier's MCPs; only these depend on LangSmith.
        logger.warning(
            "Managed tools skipped; LangSmith token unavailable",
            extra={"login": login, "status_code": exc.status_code},
        )
        return []
    return [
        _GatewayConnection(
            name=CONNECTION_NAME,
            url=_mcp_url(gateway),
            revision=gateway_id(gateway),
            updated_at="",
            headers=headers,
        )
    ]


async def _connection(login: str, gateway: str, name: str) -> MCPConnection | None:
    # Every MCP tool call looks its connection up here first; only ours needs a token.
    if name != CONNECTION_NAME:
        return None
    return next(iter(await _connections(login, gateway)), None)


def managed_mcp_source(login: str, gateway: str) -> MCPSource:
    """The workspace gateway, used as the private-thread owner, after their own MCPs."""
    login = login.strip().lower()

    async def authorize() -> None:
        owner = await private_credential_login()
        if owner is None or owner.strip().lower() != login:
            raise RuntimeError("Managed tools require the private thread owner")

    return MCPSource(
        scope="managed",
        namespace=("managed_tools", login, gateway_id(gateway)),
        list_connections=partial(_connections, login, gateway),
        get_connection=partial(_connection, login, gateway),
        authorize=authorize,
    )
