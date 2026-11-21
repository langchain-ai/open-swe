"""LangSmith Managed Tools (LMT): a person's own tools and Robin's bot kit.

Every run loads one MCP connection, ``lmt``, to a kit's route
(``/v1/managed-tools/kits/{id}/mcp``). A private thread loads the people's kit
``LMT_KIT_ID``, the workspace services an admin picked for people in LangSmith, called
with the owner's own LangSmith sign-in. It lists the tools of services the owner has not
connected yet too, and calling one of those asks them to connect it. A workspace kit
cannot link a server a person registered for themselves, so each such server the owner
connected loads as a connection of its own, to its route
(``/v1/managed-tools/servers/{id}/mcp``), as the owner too. Without ``LMT_KIT_ID`` a
private thread loads the ``me`` kit instead, which LangSmith builds per request from every
server the owner can use, their own included. Every
other run loads the bot's kit ``LMT_BOT_KIT_ID``,
called as the LMT profile ``LMT_BOT_PROFILE_ID`` with Robin's LangSmith key, and loads
only the tools of services a workspace admin connected for that profile. Two kits let a
service sign people in one way (OAuth) and the bot another (an API key), since an LMT
server has one sign-in type.

Robin writes one thing to LMT: a personal server its owner asked for, registered as
them. The kits, workspace servers and the profile's credentials are managed in
LangSmith, and a connect card links a person to LangSmith's own consent or API key
page.

Provider tokens, consent links and LangSmith credentials never reach the agent, a tool
argument or the sandbox.
"""

import asyncio
import hashlib
import json
import logging
import re
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from datetime import timedelta
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote

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
from openswe.encryption import EncryptionKeyMissingError
from openswe.mcp.models import MCPConnection, MCPScope
from openswe.mcp.runtime import MCPSource, refresh_mcp_catalog
from openswe.mcp.sealed import SealedListing
from openswe.mcp.transport import MCPCredentialMissing, MCPDiscoveryError, mcp_http_client

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

SERVERS_PATH = "/v1/managed-tools/servers"
KITS_PATH = "/v1/managed-tools/kits"
# The kit LangSmith builds per request from every server the caller can use.
ME_KIT = "me"
KIT_CONNECTION = "lmt"
CONSENT_SESSIONS_PATH = "/v1/agent-auth/oauth-authorization-sessions"
# LMT built-ins a person's kit leaves out, by key. Slack's runs on LangSmith's Slack bot
# and reads public channels only, as Robin's own Slack tools already do; a person's own
# Slack access is the workspace server slack_user. LMT names a built-in's kit tools
# langsmith_<key>_<tool>, and lists the built-in itself under its key.
PERSONAL_HIDDEN_BUILTINS = ("slack",)
_TIMEOUT_SECONDS = 30
_MAX_PAGES = 5
# A caller's LangSmith listings are answered from a cached copy, kept up to a day and read
# again in the background once a minute old; Robin reads them again at once after a change
# it makes. Only a connect click asks LangSmith directly (see ``connect_link``).
_LISTING_FRESH_FOR = timedelta(minutes=1)
_LISTING_MAX_AGE = timedelta(hours=24)
# LangSmith holds each status request open this long while consent is pending.
_CONSENT_WAIT_SECONDS = 25

type CredentialKind = Literal["none", "oauth", "secret"]
type ConsentStatus = Literal["pending", "completed", "failed", "expired"]


class ManagedToolsError(ValueError):
    """A failure safe to show the person who asked."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        """LangSmith's HTTP status, when the failure is a refused request."""


class LangSmithNotConnected(ManagedToolsError):
    def __init__(self) -> None:
        super().__init__("Sign in with LangSmith again first")


class ConsentLink(BaseModel):
    """LMT's single-use link for connecting one service; whoever completes it connects the
    service for the link's owner (a person, or a workspace bot), so it goes only to them."""

    url: str
    auth_id: str | None = None


class NewServer(BaseModel):
    """A remote MCP server a person registers for themselves alone."""

    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=2048)
    """Its HTTPS Streamable HTTP endpoint; LangSmith checks and calls it, Robin never does."""
    kind: CredentialKind = "oauth"
    header: str = Field(default="Authorization", min_length=1, max_length=100)
    """The request header an API key goes in, when ``kind`` is ``secret``."""

    def registration(self) -> dict[str, object]:
        """LMT's request to register it, visible only to the caller."""
        connection: dict[str, object] = {"type": self.kind}
        if self.kind == "secret":
            connection["fields"] = [
                {"name": self.header, "injection": {"type": "header", "name": self.header}}
            ]
        return {
            "name": self.name.strip(),
            "upstream_url": self.url.strip(),
            "connection": connection,
            "scope": "personal",
        }


class ManagedServer(BaseModel):
    """One LMT server, as one caller sees it."""

    id: str
    """LMT's server ID, or the key of a LangSmith built-in."""
    name: str
    kind: CredentialKind = "none"
    enabled: bool = True
    connected: bool = False
    has_credential: bool = False
    """The caller has stored a credential for it, which disconnecting removes."""
    personal: bool = False
    """The caller registered it for themselves; nobody else sees it."""

    icon_url: str | None = None
    icon_dark_url: str | None = None

    @classmethod
    def from_lmt(cls, item: object) -> ManagedServer | None:
        if not isinstance(item, dict):
            return None
        server_id, name = item.get("id"), item.get("name")
        if not isinstance(server_id, str) or not isinstance(name, str) or not server_id:
            return None
        connection = item.get("connection")
        kind = connection.get("type") if isinstance(connection, dict) else None
        icon_url, icon_dark_url = item.get("icon_url"), item.get("icon_dark_url")
        return cls(
            id=server_id,
            name=name,
            kind=kind if kind in ("oauth", "secret") else "none",
            enabled=item.get("enabled") is not False,
            connected=item.get("connected") is True,
            has_credential=item.get("has_credential") is True,
            personal=item.get("scope") == "personal",
            icon_url=icon_url if isinstance(icon_url, str) else None,
            icon_dark_url=icon_dark_url if isinstance(icon_dark_url, str) else None,
        )

    @property
    def connection_name(self) -> str:
        """The MCP connection name, the same whoever's credential the server runs with.

        It keeps no separators: code mode calls ``mcp_<connection>_<tool>`` by a camelCase
        alias that treats "-" and "_" alike, so a separator inside the name could give two
        servers' tools one alias."""
        return re.sub(r"[^a-z0-9]+", "", self.name.lower()) or KIT_CONNECTION

    @property
    def usable(self) -> bool:
        return self.enabled and self.connected

    @property
    def needs_connect(self) -> bool:
        return self.enabled and not self.connected and self.kind != "none"

    def matches(self, service: str) -> bool:
        """Whether ``service`` names this server: its ID, or its name as people say it."""
        if service == self.id:
            return True
        key = _match_key(service)
        return bool(key) and key in _match_key(self.name)


def _match_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def managed_tools_configured() -> bool:
    """Whether people load their own managed tools with their LangSmith sign-in. Robin
    calls LangSmith only over HTTPS, so an instance signing people in elsewhere has none."""
    return langsmith_oauth_configured() and langsmith_issuer().startswith("https://")


def _failure(response: httpx2.Response) -> ManagedToolsError:
    status = response.status_code
    if status in {401, 403}:
        return ManagedToolsError("LangSmith refused access to managed tools", status)
    if status == 404:
        return ManagedToolsError("LangSmith has no such managed tools resource", status)
    if status == 409:
        return ManagedToolsError("LangSmith cannot connect this service that way", status)
    return ManagedToolsError(f"Managed tools failed (HTTP {status}); retry later", status)


def _link(body: object) -> ConsentLink | None:
    """The consent link in a credential session, or in LMT's 428 for one missing credential."""
    if not isinstance(body, dict):
        return None
    if isinstance(entries := body.get("credentials"), list) and entries:
        body = entries[0]
        if not isinstance(body, dict):
            return None
    url, auth_id = body.get("verification_url"), body.get("auth_id")
    if not isinstance(url, str) or not url.startswith("https://"):
        return None
    return ConsentLink(url=url, auth_id=auth_id if isinstance(auth_id, str) else None)


def _unreachable(exc: BaseException) -> ManagedToolsError:
    """A request that never got a LangSmith answer, so the run goes on without LMT."""
    logger.warning(
        "Managed tools request failed", extra={"error_type": type(exc).__name__}, exc_info=exc
    )
    return ManagedToolsError("Could not reach managed tools; retry later")


class _CredentialRequired(ManagedToolsError):
    def __init__(self, link: ConsentLink | None) -> None:
        super().__init__("Connect this service first")
        self.link = link


def _wrapped[E: BaseException](error: BaseException | None, kind: type[E]) -> E | None:
    """MCP transports nest HTTP failures inside exception groups, and the client re-raises
    a failed connect as a ``RuntimeError`` caused by the original."""
    if error is None or isinstance(error, kind):
        return error
    if isinstance(error, BaseExceptionGroup):
        for child in error.exceptions:
            if (found := _wrapped(child, kind)) is not None:
                return found
    return _wrapped(error.__cause__, kind)


class _KitServer(BaseModel):
    """One server a kit uses, from ``GET /kits/{kit}/connections``: whether the caller
    can use it, and the kit's tools that call it."""

    connected: bool
    # "unavailable" means LangSmith could not check, not that the caller is unconnected.
    credential_status: str | None = None
    tool_names: list[str] = []

    @property
    def unknown(self) -> bool:
        return self.credential_status == "unavailable"


class _KitServerPage(BaseModel):
    items: list[_KitServer] = []
    next_cursor: str | None = None


class _LMTConnection(MCPConnection):
    """An LMT route offering every tool it lists, carrying the caller's current
    credential in memory, never stored."""

    headers: dict[str, str] = Field(default_factory=dict, repr=False, exclude=True)
    server_id: str | None = None
    """The one LMT server this route calls; None for a kit, which calls several."""

    def connection_headers(self) -> dict[str, str]:
        return self.headers

    def allows_tool(self, name: str) -> bool:
        return True

    @property
    def offers_tools(self) -> bool:
        return True

    async def check_response(self, response: httpx2.Response) -> None:
        # LMT answers a call needing a credential with 428 (the caller connects it) or 409
        # (a profile, which only a workspace admin connects); MCP transports keep neither.
        if response.status_code in (409, 428):
            raise MCPCredentialMissing(admin_only=response.status_code == 409)


class _KitConnection(_LMTConnection):
    """The kit's route."""

    kit: str
    connected_only: bool = False
    """Leave out the tools of services the caller has no credential for, which nobody
    running as the caller could connect."""
    hidden_builtins: tuple[str, ...] = ()
    """Keys of LMT built-ins whose tools the kit's runs never load."""

    def allows_tool(self, name: str) -> bool:
        return not any(name.startswith(f"langsmith_{key}_") for key in self.hidden_builtins)

    async def unusable_tools(self) -> set[str]:
        if not self.connected_only:
            return set()
        servers = await self._servers()
        return {name for server in servers if not server.connected for name in server.tool_names}

    async def _servers(self) -> list[_KitServer]:
        """The caller's connection to each server the kit uses. A failed read, or a status
        LangSmith could not check, fails discovery rather than caching a guess as the
        kit's tools."""
        path = f"{KITS_PATH}/{quote(self.kit, safe='')}/connections"
        servers: list[_KitServer] = []
        async with mcp_http_client(
            langsmith_issuer(), timeout=httpx2.Timeout(_TIMEOUT_SECONDS)
        ) as client:
            cursor: str | None = None
            for _ in range(_MAX_PAGES):
                params = {"page_size": "100", **({"cursor": cursor} if cursor else {})}
                response = await client.get(
                    langsmith_issuer() + path, headers=self.headers, params=params
                )
                if not response.is_success:
                    raise MCPDiscoveryError(
                        f"Could not check which services the kit's caller has connected ({_failure(response)})"
                    )
                page = _KitServerPage.model_validate(response.json())
                servers.extend(page.items)
                if (cursor := page.next_cursor) is None:
                    break
            else:
                raise MCPDiscoveryError("LangSmith returned too many pages of kit connections")
        if any(server.unknown for server in servers):
            raise MCPDiscoveryError(
                "LangSmith could not check which services the kit's caller has connected"
            )
        return servers


class ManagedTools(ABC):
    """What one LangSmith caller reaches in LMT, offered to runs as MCP connections."""

    scope: MCPScope
    namespace: tuple[str, ...]

    @abstractmethod
    async def headers(self) -> dict[str, str]:
        """How LMT identifies the caller; raises :class:`LangSmithNotConnected`."""

    @abstractmethod
    async def _authorize(self) -> None:
        """Refuse a tool call the run may not make with this caller's credentials."""

    @abstractmethod
    def configured(self) -> bool:
        """Whether this instance loads the kit as this caller."""

    @abstractmethod
    def kit(self) -> str | None:
        """The kit this caller's runs load."""

    connected_only: bool = False
    hidden_builtins: tuple[str, ...] = ()
    """Keys of LMT built-ins left out of this caller's kit: neither their tools nor an
    offer to connect them."""
    personal_servers: bool = False
    """Whether runs also load the servers the caller registered for themselves, which a
    workspace kit cannot link."""

    def _tenant(self) -> dict[str, str]:
        # Without a pin, LangSmith uses the workspace the credential carries.
        return {"X-Tenant-Id": tenant} if (tenant := ENV.LMT_TENANT_ID.optional()) else {}

    def _client(self) -> httpx2.AsyncClient:
        return mcp_http_client(langsmith_issuer(), timeout=httpx2.Timeout(_TIMEOUT_SECONDS))

    async def _request(
        self, method: str, path: str, *, body: object = None, ok: Iterable[int] = ()
    ) -> httpx2.Response:
        headers = await self.headers()
        try:
            async with self._client() as client:
                response = await client.request(
                    method, langsmith_issuer() + path, headers=headers, json=body
                )
        except (httpx2.HTTPError, MCPDiscoveryError) as exc:
            raise _unreachable(exc) from None
        if not response.is_success and response.status_code not in set(ok):
            raise _failure(response)
        return response

    async def _pages(self, path: str, headers: dict[str, str] | None = None) -> list[object]:
        """Every page of a listing, with the caller's headers unless ``headers`` replaces them."""
        items: list[object] = []
        headers = headers or await self.headers()
        try:
            async with self._client() as client:
                cursor: str | None = None
                for _ in range(_MAX_PAGES):
                    params = {"page_size": "100", **({"cursor": cursor} if cursor else {})}
                    response = await client.get(
                        langsmith_issuer() + path, headers=headers, params=params
                    )
                    if not response.is_success:
                        raise _failure(response)
                    payload = response.json()
                    items.extend(payload.get("items", []))
                    cursor = payload.get("next_cursor")
                    if not cursor:
                        break
        except (httpx2.HTTPError, MCPDiscoveryError, ValueError) as exc:
            if isinstance(exc, ManagedToolsError):
                raise
            raise _unreachable(exc) from None
        return items

    def _loaders(self) -> dict[str, Callable[[], Awaitable[object]]]:
        """Each cached LangSmith listing, by name, as the JSON its loader stores."""
        loaders: dict[str, Callable[[], Awaitable[object]]] = {"servers": self._load_servers}
        if not self.built_per_caller:
            loaders["kit"] = self._load_kit_servers
        return loaders

    @property
    def built_per_caller(self) -> bool:
        """Whether LangSmith builds the kit from every server the caller can use, leaving
        no stored links to read."""
        return self.kit() == ME_KIT

    async def _load_kit_servers(self) -> list[str]:
        """The IDs (or built-in keys) of the kit's servers the caller may call. They come
        from the kit's connections, which apply the caller's own access to each server;
        the kit's record needs unrestricted MCP access, which an access policy takes away."""
        kit = self.kit()
        if kit is None:
            return []
        items = await self._pages(f"{KITS_PATH}/{quote(kit, safe='')}/connections")
        return sorted(
            {
                server_id
                for item in items
                if isinstance(item, dict) and isinstance(server_id := item.get("id"), str)
            }
        )

    async def kit_servers(self, *, fresh: bool = False) -> list[ManagedServer]:
        """The servers whose tools the caller's runs load, the kit's and their own, with
        the caller's state of each; ``fresh`` asks LangSmith now rather than take the
        cached copy."""
        linked: set[str] | None = None
        if not self.built_per_caller:
            cached = await self._cached("kit", fresh=fresh)
            linked = set(cached) if isinstance(cached, list) else set()
        return [
            server
            for server in await self.servers(fresh=fresh)
            if server.id not in self.hidden_builtins
            and (linked is None or server.id in linked or self._loads_own(server))
        ]

    def _loads_own(self, server: ManagedServer) -> bool:
        """Whether runs load ``server`` on its own route: one the caller registered, which
        no fixed kit can link."""
        return self.personal_servers and not self.built_per_caller and server.personal

    async def _listing(self, listing: str) -> SealedListing:
        """A listing's cache entry, per caller, encrypted (see :mod:`openswe.mcp.sealed`)."""
        digest = hashlib.sha256(json.dumps(self.namespace).encode()).hexdigest()
        return SealedListing(
            f"lmt:{listing}:{digest}",
            self._loaders()[listing],
            fresh_for=_LISTING_FRESH_FOR,
            max_age=_LISTING_MAX_AGE,
        )

    async def _cached(self, listing: str, *, fresh: bool = False) -> object:
        """A listing shared by every replica, so runs and tool calls do not each ask
        LangSmith; :meth:`refresh` re-reads it after a connection changes. ``fresh`` reads
        it from LangSmith now, as does a cached one that can't be read."""
        try:
            value = await (await self._listing(listing)).read(fresh=fresh)
        except EncryptionKeyMissingError:
            raise ManagedToolsError(
                "Managed tools are not configured on this Robin instance"
            ) from None
        if value is None:
            raise ManagedToolsError("Could not read managed tools; retry later")
        return value

    async def _load_servers(self) -> list[dict[str, object]]:
        servers = [ManagedServer.from_lmt(item) for item in await self._pages(SERVERS_PATH)]
        return [server.model_dump(mode="json") for server in servers if server is not None]

    async def servers(self, *, fresh: bool = False) -> list[ManagedServer]:
        """Every server the caller can see in the LangSmith workspace, with its state;
        ``fresh`` asks LangSmith now rather than take the cached copy."""
        cached = await self._cached("servers", fresh=fresh)
        items = cached if isinstance(cached, list) else []
        return [ManagedServer.model_validate(item) for item in items]

    async def server(self, server_id: str, *, fresh: bool = False) -> ManagedServer:
        found = next(
            (item for item in await self.servers(fresh=fresh) if item.id == server_id), None
        )
        if found is None:
            raise ManagedToolsError("LangSmith has no such service")
        return found

    async def refresh(self, *, needed: Iterable[str] = ()) -> None:
        """Re-read the listings now, then each connection's tools, so a service that was
        just connected or disconnected shows in the next run. The kit failing to list
        raises, as does one of the caller's own servers in ``needed`` (the one a consent
        just connected); another of theirs failing keeps its cached tools, listed again
        once stale, rather than holding up a change to a different service."""
        needed = set(needed)
        for listing in self._loaders():
            await (await self._listing(listing)).refresh()
        source = self.source()
        for connection in await self.connections():
            try:
                await refresh_mcp_catalog(source, connection.name)
            except Exception:
                if connection.name == KIT_CONNECTION or (
                    isinstance(connection, _LMTConnection) and connection.server_id in needed
                ):
                    raise
                logger.warning(
                    "Managed tools could not list a server's tools again",
                    extra={"mcp_name": connection.name},
                    exc_info=True,
                )

    async def refresh_quietly(self) -> None:
        """:meth:`refresh` after a change LangSmith already made; a cached listing is read
        again in the background once a minute old, so a failure is only logged."""
        try:
            await self.refresh()
        except Exception:
            logger.warning("Managed tools refresh after a change failed", exc_info=True)

    async def connections(self) -> list[MCPConnection]:
        """Resolved on every lookup, so each tool call carries a fresh credential.

        Failures leave out only what depends on LangSmith: raising would make the runtime
        drop every tier's MCPs. One listing the caller's own servers leaves out only those.
        """
        kit = self.kit()
        if kit is None or not self.configured():
            return []
        try:
            headers = await self.headers()
            revision = await self.revision(kit)
        except LangSmithNotConnected:
            logger.info("Managed tools skipped; LangSmith not connected")
            return []
        except LangSmithOAuthError as exc:
            logger.warning(
                "Managed tools skipped; LangSmith token unavailable",
                extra={"status_code": exc.status_code},
            )
            return []
        except ManagedToolsError:
            logger.warning("Managed tools skipped", exc_info=True)
            return []
        kit_connection = _KitConnection(
            name=KIT_CONNECTION,
            kit=kit,
            url=f"{langsmith_issuer()}{KITS_PATH}/{quote(kit, safe='')}/mcp",
            revision=revision,
            headers=headers,
            connected_only=self.connected_only,
            hidden_builtins=self.hidden_builtins,
        )
        return [kit_connection, *await self._own_connections(headers)]

    async def _own_connections(self, headers: dict[str, str]) -> list[MCPConnection]:
        """A connection to each server the caller registered and connected, beside a fixed
        kit. One not connected yet lists no tools; the run offers to connect it instead."""
        try:
            servers = [
                server
                for server in await self.servers()
                if self._loads_own(server) and server.usable
            ]
        except ManagedToolsError, LangSmithOAuthError:
            logger.warning("Managed tools left out the caller's own servers", exc_info=True)
            return []
        names = Counter(server.connection_name for server in servers)
        connections: list[MCPConnection] = []
        for server in servers:
            name = server.connection_name
            # Two of the caller's servers, or one and the kit, would otherwise share a name.
            if name == KIT_CONNECTION or names[name] > 1:
                name = f"{name}{hashlib.sha256(server.id.encode()).hexdigest()[:8]}"
            route = f"{SERVERS_PATH}/{quote(server.id, safe='')}/mcp"
            connections.append(
                _LMTConnection(
                    name=name,
                    server_id=server.id,
                    url=langsmith_issuer() + route,
                    revision=await self.revision(route),
                    headers=headers,
                )
            )
        return connections

    async def revision(self, route: str) -> str:
        """What a route's cached tool list is kept under: its kit (or server route) alone,
        so a change to its servers never leaves a run with nothing cached. :meth:`refresh`
        lists it again after a change made here; one made in LangSmith shows once the list
        goes stale."""
        return hashlib.sha256(route.encode()).hexdigest()[:16]

    async def connection(self, name: str) -> MCPConnection | None:
        return next((item for item in await self.connections() if item.name == name), None)

    def source(self) -> MCPSource:
        return MCPSource(
            scope=self.scope,
            namespace=self.namespace,
            list_connections=self.connections,
            get_connection=self.connection,
            authorize=self._authorize,
        )

    def _mcp_failure(self, response: httpx2.Response) -> ManagedToolsError:
        if response.status_code == 428:
            try:
                body: object = response.json()
            except ValueError:
                body = None
            return _CredentialRequired(_link(body))
        return _failure(response)

    async def _mcp[T](
        self, path: str, use: Callable[[Client[StreamableHttpTransport]], Awaitable[T]]
    ) -> T:
        """One MCP session with an LMT route, its HTTP failures raised as ours."""
        url = langsmith_issuer() + path

        async def check(response: httpx2.Response) -> None:
            if response.status_code >= 300:
                await response.aread()
                raise self._mcp_failure(response)

        def factory(
            headers: dict[str, str] | None = None,
            timeout: httpx2.Timeout | None = None,
            auth: httpx2.Auth | None = None,
            follow_redirects: bool = False,
        ) -> httpx2.AsyncClient:
            client = mcp_http_client(url, headers, timeout, auth, follow_redirects)
            client.event_hooks["response"].append(check)
            return client

        transport = StreamableHttpTransport(
            url, headers=await self.headers(), httpx_client_factory=factory
        )
        try:
            async with asyncio.timeout(_TIMEOUT_SECONDS):
                async with Client(transport, timeout=_TIMEOUT_SECONDS) as client:
                    return await use(client)
        except Exception as exc:
            if (found := _wrapped(exc, ManagedToolsError)) is not None:
                raise found from None
            logger.warning(
                "Managed tools MCP session failed", extra={"lmt_path": path}, exc_info=True
            )
            raise ManagedToolsError("Could not reach managed tools; retry later") from None

    async def consent_status(self, auth_id: str) -> ConsentStatus:
        """Where one consent link stands, waiting up to LangSmith's long poll for it to be
        used (or abandoned) when it is still pending."""
        headers = await self.headers()
        url = f"{langsmith_issuer()}{CONSENT_SESSIONS_PATH}/{quote(auth_id, safe='')}"
        timeout = httpx2.Timeout(_TIMEOUT_SECONDS + _CONSENT_WAIT_SECONDS)
        async with mcp_http_client(langsmith_issuer(), timeout=timeout) as client:
            response = await client.get(
                url, headers=headers, params={"wait_seconds": str(_CONSENT_WAIT_SECONDS)}
            )
        if not response.is_success:
            raise ManagedToolsError(f"Consent status unavailable (HTTP {response.status_code})")
        status = response.json().get("status")
        if status not in ("pending", "completed", "failed", "expired"):
            raise ManagedToolsError("LangSmith returned an unknown consent status")
        return status


class PersonalTools(ManagedTools):
    """LMT as one person, with their own LangSmith sign-in.

    Their private threads load the people's kit ``LMT_KIT_ID`` and the servers they
    registered themselves, or the ``me`` kit, every server they can use, without it;
    calling a tool of a service they have not connected yet asks them to connect it.
    """

    scope: MCPScope = "managed"
    hidden_builtins = PERSONAL_HIDDEN_BUILTINS
    personal_servers = True

    def __init__(self, login: str) -> None:
        self.login = login.strip().lower()
        self.namespace = ("managed_tools", self.login)

    def configured(self) -> bool:
        return managed_tools_configured()

    def kit(self) -> str | None:
        return ENV.LMT_KIT_ID.optional() or ME_KIT

    async def headers(self) -> dict[str, str]:
        if not langsmith_oauth_configured():
            raise ManagedToolsError("Managed tools are not configured on this Robin instance")
        token = await langsmith_access_token(self.login)
        if token is None:
            raise LangSmithNotConnected
        return {"Authorization": f"Bearer {token}", **self._tenant()}

    async def _authorize(self) -> None:
        owner = await private_credential_login()
        if owner is None or owner.strip().lower() != self.login:
            raise RuntimeError("Personal managed tools require the private thread owner")

    async def connectable(self) -> list[str]:
        """The names of the kit's services this person could connect, for a run's prompt;
        empty when LangSmith cannot say, since the run goes on without them."""
        if not self.configured():
            return []
        try:
            return [server.name for server in await self.unconnected()]
        except LangSmithNotConnected:
            return []
        except ManagedToolsError, LangSmithOAuthError:
            logger.warning("Managed tools services to connect unavailable", exc_info=True)
            return []

    async def unconnected(self, services: Iterable[str] = ()) -> list[ManagedServer]:
        """The kit's services this person could connect, narrowed to ``services`` when
        named."""
        wanted = list(services)
        return [
            server
            for server in await self.kit_servers()
            if server.needs_connect and (not wanted or any(server.matches(s) for s in wanted))
        ]

    async def connect_link(self, server_id: str) -> ConsentLink | None:
        """A fresh link connecting one service for this person; None when it already works.
        Asks LangSmith now: a cached copy could still call a service connected that someone
        has since disconnected in LangSmith."""
        server = await self.server(server_id, fresh=True)
        if server.usable:
            return None
        if server.kind == "secret":
            response = await self._request(
                "POST", f"{SERVERS_PATH}/{quote(server.id, safe='')}/credential-sessions"
            )
            if (link := _link(response.json())) is None:
                raise ManagedToolsError("LangSmith did not return a setup link; retry")
            # Its status lives elsewhere in Agent Auth, so nothing waits on it; the cached
            # listing, read again once a minute old, shows the service connected.
            return ConsentLink(url=link.url)

        async def ping(client: Client[StreamableHttpTransport]) -> None:
            await client.list_tools_mcp()

        # LMT answers an OAuth server's first request with 428 and the consent link.
        try:
            await self._mcp(f"{SERVERS_PATH}/{quote(server.id, safe='')}/mcp", ping)
        except _CredentialRequired as required:
            if required.link is None:
                raise ManagedToolsError("LangSmith did not return a consent link; retry") from None
            return required.link
        await self.refresh()
        return None

    async def disconnect(self, server_id: str) -> None:
        """Delete this person's own credential for one service; LangSmith keeps the server."""
        server = await self.server(server_id)
        response = await self._request(
            "DELETE", f"{SERVERS_PATH}/{quote(server.id, safe='')}/credential", ok=(404, 409)
        )
        if response.status_code == 404:
            raise ManagedToolsError("This service no longer exists in LangSmith")
        if response.status_code == 409:
            raise ManagedToolsError("This service has no connection to remove")
        await self.refresh()

    async def add_server(self, new: NewServer) -> ManagedServer:
        """Register a server only this person sees; its tools load in their next run."""
        response = await self._request(
            "POST", SERVERS_PATH, body=new.registration(), ok=(400, 403, 409)
        )
        if response.status_code == 400:
            raise ManagedToolsError(
                "LangSmith refused this server; check that its URL is a public HTTPS MCP "
                "endpoint and that the sign-in type matches what it asks for",
                400,
            )
        if response.status_code == 403:
            raise ManagedToolsError(
                "Your LangSmith role cannot add personal MCP servers; ask a workspace admin",
                403,
            )
        if response.status_code == 409:
            raise ManagedToolsError("You already have a server with this name or URL", 409)
        server = ManagedServer.from_lmt(response.json())
        if server is None:
            raise ManagedToolsError("LangSmith did not return the new server; retry")
        # LangSmith has the server now, so a failed refresh must not report it unadded.
        await self.refresh_quietly()
        return server

    async def remove_server(self, server_id: str) -> None:
        """Delete a server this person registered, and their credential for it."""
        server = await self.server(server_id)
        if not server.personal:
            raise ManagedToolsError("Only a server you added can be removed here")
        await self._request("DELETE", f"{SERVERS_PATH}/{quote(server.id, safe='')}", ok=(404,))
        await self.refresh_quietly()


class BotTools(ManagedTools):
    """LMT as Robin's bot: its own kit ``LMT_BOT_KIT_ID``, called as the LMT profile
    ``LMT_BOT_PROFILE_ID``, with the credentials a workspace admin connected for it in
    LangSmith.

    Every run that is not a private thread loads it, public threads, Slack channels and
    automations included. Only the tools of services the profile has a credential for
    load, since nobody in a run could connect the rest.
    """

    scope: MCPScope = "managed"
    connected_only = True

    def __init__(self) -> None:
        self.profile = ENV.LMT_BOT_PROFILE_ID.optional() or None
        self.namespace = ("managed_tools", "bot", self.profile or "")

    def configured(self) -> bool:
        return (
            self.kit() is not None and self.profile is not None and ENV.LANGSMITH_API_KEY.is_set()
        )

    def kit(self) -> str | None:
        return ENV.LMT_BOT_KIT_ID.optional() or None

    async def headers(self) -> dict[str, str]:
        if self.profile is None or not ENV.LANGSMITH_API_KEY.is_set():
            raise ManagedToolsError("Robin has no LMT bot profile")
        return {
            "X-Api-Key": ENV.LANGSMITH_API_KEY.get(),
            "X-LangSmith-Profile": self.profile,
            **self._tenant(),
        }

    async def _authorize(self) -> None:
        """Any run may call the bot's tools."""
        return


class BotKitViewer(PersonalTools):
    """Robin's bot kit as one admin reads it, to show which services the bot can use.

    LMT lets only a user read a kit, and the bot calls LMT with an API key, so the admin's
    own LangSmith sign-in reads the kit's links. Each server's state comes from the bot's
    own listing, as its profile, so it is what the bot can use and asks nothing of the
    admin's LangSmith grants.
    """

    # The bot kit as it is, whatever a person's own kit leaves out or adds.
    hidden_builtins = ()
    personal_servers = False

    def __init__(self, login: str) -> None:
        super().__init__(login)
        self.bot = BotTools()
        self.namespace = ("managed_tools", "bot-viewer", self.login)

    def configured(self) -> bool:
        return self.bot.configured() and langsmith_oauth_configured()

    def kit(self) -> str | None:
        return self.bot.kit()

    async def servers(self, *, fresh: bool = False) -> list[ManagedServer]:
        try:
            return await self.bot.servers(fresh=fresh)
        except ManagedToolsError as exc:
            # Only a forbidden listing falls back: a deployment key may invoke the kit
            # without mcp-servers:read. A revoked key or an outage stays an error, so the
            # page never reports a broken bot as healthy.
            if exc.status != 403:
                raise
            logger.warning(
                "Bot key may not list managed tools servers; listing as the admin",
                exc_info=True,
            )
        # As the admin naming the bot's profile, which needs their LangSmith
        # credential-metadata grant; uncached, so a revoked grant applies at once.
        return await self._servers_as_bot_profile()

    async def _load_kit_servers(self) -> list[str]:
        """The servers the bot kit links, from the kit's record, which the admin may read
        with ``mcp-servers:read`` alone; its connections would need them to invoke tools."""
        kit = self.kit()
        if kit is None:
            return []
        body = (await self._request("GET", f"{KITS_PATH}/{quote(kit, safe='')}")).json()
        links = body.get("tools") if isinstance(body, dict) else None
        return sorted(
            {
                source
                for link in (links if isinstance(links, list) else [])
                if isinstance(link, dict)
                and isinstance(
                    source := link.get("server_id") or link.get("builtin_server_key"), str
                )
            }
        )

    async def _servers_as_bot_profile(self) -> list[ManagedServer]:
        if self.bot.profile is None:
            raise ManagedToolsError("Robin has no LMT bot profile")
        headers = {**await self.headers(), "X-LangSmith-Profile": self.bot.profile}
        servers = [
            ManagedServer.from_lmt(item) for item in await self._pages(SERVERS_PATH, headers)
        ]
        return [server for server in servers if server is not None]
