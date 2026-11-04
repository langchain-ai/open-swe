"""LangSmith Managed Tools (LMT): each workspace's bot, and each person's own connections.

Every LMT server a run uses is one MCP connection to that server's own route
(``/v1/managed-tools/servers/{id}/mcp``), named after the server. A person's own
connection to a server therefore replaces the workspace bot's in their private threads,
by the runtime's usual precedence.

- The workspace bot runs the tools in the workspace's tool kit as the workspace's LMT
  profile (``X-LangSmith-Profile``), with Open SWE's own LangSmith API key. Only admins
  connect a profile's services, in LangSmith or from the workspace's settings.
- A person's own connections are every LMT server they connected in LangSmith, called
  with their own LangSmith sign-in; they load only in that person's private threads.

Provider tokens, consent links and LangSmith credentials never reach the agent, a tool
argument or the sandbox.
"""

import asyncio
import hashlib
import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Literal
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
from openswe.mcp.models import MCPConnection, MCPScope
from openswe.mcp.runtime import MCPSource, refresh_mcp_catalog
from openswe.mcp.transport import mcp_http_client

if TYPE_CHECKING:
    from langgraph_api.cache import SWRResult

logger = logging.getLogger(__name__)

KITS_PATH = "/v1/managed-tools/kits"
SERVERS_PATH = "/v1/managed-tools/servers"
PROFILES_PATH = "/v1/managed-tools/profiles"
CONSENT_SESSIONS_PATH = "/v1/agent-auth/oauth-authorization-sessions"
_MISSING_CREDENTIALS_META = "com.langchain.langsmith/missingCredentials"
_TIMEOUT_SECONDS = 30
_MAX_PAGES = 5
_LISTING_FRESH_FOR = timedelta(minutes=1)
_LISTING_MAX_AGE = timedelta(minutes=10)
# LangSmith holds each status request open this long while consent is pending.
_CONSENT_WAIT_SECONDS = 25
# Consent links expire after ten minutes; stop waiting shortly after.
_CONSENT_DEADLINE_SECONDS = 11 * 60
_CONSENT_POLL_FLOOR_SECONDS = 1

type CredentialKind = Literal["none", "oauth", "secret"]
type ConsentOutcome = Literal["completed", "failed", "expired"]


class ManagedToolsError(ValueError):
    """A failure safe to show the person who asked."""


class LangSmithNotConnected(ManagedToolsError):
    def __init__(self) -> None:
        super().__init__("Sign in with LangSmith again first")


class ConsentLink(BaseModel):
    """LMT's single-use link for connecting one service; whoever completes it connects the
    service for the link's owner (a person, or a workspace bot), so it goes only to them."""

    url: str
    auth_id: str | None = None


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

    @classmethod
    def from_lmt(cls, item: object) -> ManagedServer | None:
        if not isinstance(item, dict):
            return None
        server_id, name = item.get("id"), item.get("name")
        if not isinstance(server_id, str) or not isinstance(name, str) or not server_id:
            return None
        connection = item.get("connection")
        kind = connection.get("type") if isinstance(connection, dict) else None
        return cls(
            id=server_id,
            name=name,
            kind=kind if kind in ("oauth", "secret") else "none",
            enabled=item.get("enabled") is not False,
            connected=item.get("connected") is True,
            has_credential=item.get("has_credential") is True,
        )

    @property
    def connection_name(self) -> str:
        """The MCP connection name, the same whoever's credential the server runs with."""
        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-") or "lmt"

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


class Kit(BaseModel):
    id: str
    name: str
    tool_count: int = 0

    @classmethod
    def from_lmt(cls, item: dict[str, object]) -> Kit:
        count, tools = item.get("tool_count"), item.get("tools")
        return cls(
            id=str(item["id"]),
            name=str(item.get("name") or item["id"]),
            tool_count=count
            if isinstance(count, int)
            else len(tools)
            if isinstance(tools, list)
            else 0,
        )


class Profile(BaseModel):
    id: str
    name: str


class BotServerStatus(BaseModel):
    """One server in a workspace's tool kit, and whether the workspace bot can use it."""

    id: str
    name: str
    tool_count: int
    connected: bool


class BotStatus(BaseModel):
    kit: Kit
    profile_id: str
    servers: list[BotServerStatus]

    @property
    def missing(self) -> list[BotServerStatus]:
        return [server for server in self.servers if not server.connected]


def lmt_id(value: str, what: str = "tool kit") -> str:
    try:
        return str(UUID(value))
    except ValueError:
        raise ManagedToolsError(f"Unknown {what}") from None


def managed_tools_configured() -> bool:
    return langsmith_oauth_configured()


def _failure(response: httpx2.Response) -> ManagedToolsError:
    if response.status_code in {401, 403}:
        return ManagedToolsError("LangSmith refused access to managed tools")
    if response.status_code == 404:
        return ManagedToolsError("LangSmith has no such managed tools resource")
    if response.status_code == 409:
        return ManagedToolsError("LangSmith cannot connect this service that way")
    return ManagedToolsError(f"Managed tools failed (HTTP {response.status_code}); retry later")


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


class _CredentialRequired(ManagedToolsError):
    def __init__(self, link: ConsentLink | None) -> None:
        super().__init__("Connect this service first")
        self.link = link


def _wrapped[E: BaseException](error: BaseException, kind: type[E]) -> E | None:
    """MCP transports nest HTTP failures inside exception groups."""
    if isinstance(error, kind):
        return error
    if isinstance(error, BaseExceptionGroup):
        for child in error.exceptions:
            if (found := _wrapped(child, kind)) is not None:
                return found
    return None


class _ServerConnection(MCPConnection):
    """One LMT server's route, carrying the caller's current credential in memory, never
    stored. ``tools`` limits it to a tool kit's tools; ``None`` offers every tool."""

    headers: dict[str, str] = Field(default_factory=dict, repr=False, exclude=True)
    tools: frozenset[str] | None = Field(default=None, exclude=True)

    def connection_headers(self) -> dict[str, str]:
        return self.headers

    def allows_tool(self, name: str) -> bool:
        return self.tools is None or name in self.tools

    @property
    def offers_tools(self) -> bool:
        return True


@dataclass(frozen=True)
class _Offer:
    """A server whose tools a caller's runs load, and which of them (``None``: all)."""

    server: ManagedServer
    tools: frozenset[str] | None = None


class ManagedTools(ABC):
    """What one LangSmith caller reaches in LMT, offered to runs as MCP connections."""

    scope: MCPScope
    namespace: tuple[str, ...]

    @abstractmethod
    async def headers(self) -> dict[str, str]:
        """How LMT identifies the caller; raises :class:`LangSmithNotConnected`."""

    @abstractmethod
    async def _offers(self) -> list[_Offer]:
        """The servers whose tools runs load, as of the cached listings."""

    @abstractmethod
    async def _authorize(self) -> None:
        """Refuse a tool call the run may not make with this caller's credentials."""

    def _tenant(self) -> dict[str, str]:
        # Without a pin, LangSmith uses the workspace the credential carries.
        return {"X-Tenant-Id": tenant} if (tenant := ENV.LMT_TENANT_ID.optional()) else {}

    def _client(self) -> httpx2.AsyncClient:
        return mcp_http_client(langsmith_issuer(), timeout=httpx2.Timeout(_TIMEOUT_SECONDS))

    async def _request(
        self, method: str, path: str, *, body: object = None, ok: Iterable[int] = ()
    ) -> httpx2.Response:
        headers = await self.headers()
        async with self._client() as client:
            response = await client.request(
                method, langsmith_issuer() + path, headers=headers, json=body
            )
        if not response.is_success and response.status_code not in set(ok):
            raise _failure(response)
        return response

    async def _pages(self, path: str) -> list[object]:
        items: list[object] = []
        headers = await self.headers()
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
        return items

    def _loaders(self) -> dict[str, Callable[[], Awaitable[object]]]:
        """Each cached LangSmith listing, by name, as the JSON its loader stores."""
        return {"servers": self._load_servers}

    async def _swr(self, listing: str) -> SWRResult[object]:
        from langgraph_api.cache import swr

        digest = hashlib.sha256(json.dumps(self.namespace).encode()).hexdigest()
        return await swr(
            f"lmt:{listing}:{digest}",
            self._loaders()[listing],
            fresh_for=_LISTING_FRESH_FOR,
            max_age=_LISTING_MAX_AGE,
        )

    async def _cached(self, listing: str) -> object:
        """A listing shared by every replica for a minute, so runs and tool calls do not
        each ask LangSmith; :meth:`refresh` re-reads it after a connection changes."""
        return (await self._swr(listing)).value

    async def _load_servers(self) -> list[dict[str, object]]:
        servers = [ManagedServer.from_lmt(item) for item in await self._pages(SERVERS_PATH)]
        return [server.model_dump(mode="json") for server in servers if server is not None]

    async def servers(self) -> list[ManagedServer]:
        """Every server the caller can see in the LangSmith workspace, with its state."""
        cached = await self._cached("servers")
        items = cached if isinstance(cached, list) else []
        return [ManagedServer.model_validate(item) for item in items]

    async def refresh(self) -> None:
        """Re-read the listings now, then each offered server's tools, so a service that
        was just connected or disconnected shows in the next run."""
        for listing in self._loaders():
            result = await self._swr(listing)
            if result.status != "miss":
                await result.mutate()
        source = self.source()
        for connection in await self.connections():
            await refresh_mcp_catalog(source, connection.name)

    async def connections(self) -> list[MCPConnection]:
        """Resolved on every lookup, so each tool call carries a fresh credential.

        Failures leave out only these servers: raising would make the runtime drop
        every tier's MCPs, and only these depend on LangSmith.
        """
        if not managed_tools_configured():
            return []
        try:
            headers = await self.headers()
            offers = await self._offers()
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
            logger.warning("Managed tools skipped; LangSmith listing failed", exc_info=True)
            return []
        connections: dict[str, MCPConnection] = {}
        for offer in offers:
            server = offer.server
            name = server.connection_name
            if name in connections:
                # LangSmith lets a registered server share a built-in's name (Linear, Pylon).
                name = f"{name}-{server.id[:8].lower()}"
            connections[name] = _ServerConnection(
                name=name,
                url=f"{langsmith_issuer()}{SERVERS_PATH}/{quote(server.id, safe='')}/mcp",
                revision=hashlib.sha256(
                    json.dumps([server.id, sorted(offer.tools or [])]).encode()
                ).hexdigest()[:16],
                updated_at="",
                headers=headers,
                tools=offer.tools,
            )
        return list(connections.values())

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
            raise ManagedToolsError("Could not reach managed tools; retry later") from None

    async def consent_outcome(self, auth_id: str) -> ConsentOutcome:
        """Wait for whoever was sent one consent link to finish (or abandon) it."""
        headers = await self.headers()
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
                    # Long polls return early sometimes; never hammer LangSmith.
                    await asyncio.sleep(_CONSENT_POLL_FLOOR_SECONDS)


class PersonalTools(ManagedTools):
    """LMT as one person, with their own LangSmith sign-in.

    Their runs load every server they have connected; the rest are theirs to connect.
    The same sign-in reads and sets up workspace bots, which LangSmith permits only to
    workspace admins.
    """

    scope: MCPScope = "managed"

    def __init__(self, login: str) -> None:
        self.login = login.strip().lower()
        self.namespace = ("managed_tools", self.login)

    async def headers(self) -> dict[str, str]:
        if not managed_tools_configured():
            raise ManagedToolsError("Managed tools are not configured on this Open SWE instance")
        token = await langsmith_access_token(self.login)
        if token is None:
            raise LangSmithNotConnected
        return {"Authorization": f"Bearer {token}", **self._tenant()}

    async def _authorize(self) -> None:
        owner = await private_credential_login()
        if owner is None or owner.strip().lower() != self.login:
            raise RuntimeError("Personal managed tools require the private thread owner")

    async def _offers(self) -> list[_Offer]:
        # Public servers count as connected for everyone; only those the person connected
        # an account to are theirs. A workspace puts public ones in its bot's kit.
        return [
            _Offer(server)
            for server in await self.servers()
            if server.usable and server.has_credential
        ]

    async def server(self, server_id: str) -> ManagedServer:
        found = next((item for item in await self.servers() if item.id == server_id), None)
        if found is None:
            raise ManagedToolsError("LangSmith has no such service for you")
        return found

    async def unconnected(self, services: Iterable[str] = ()) -> list[ManagedServer]:
        """The services this person could connect, narrowed to ``services`` when named."""
        wanted = list(services)
        return [
            server
            for server in await self.servers()
            if server.needs_connect and (not wanted or any(server.matches(s) for s in wanted))
        ]

    async def connect_link(self, server_id: str) -> ConsentLink | None:
        """A fresh link connecting one service for this person; None when it already works."""
        server = await self.server(server_id)
        if server.usable:
            return None
        if server.kind == "secret":
            response = await self._request(
                "POST", f"{SERVERS_PATH}/{quote(server.id, safe='')}/credential-sessions"
            )
            if (link := _link(response.json())) is None:
                raise ManagedToolsError("LangSmith did not return a setup link; retry")
            # Its status lives elsewhere in Agent Auth, so nothing waits on it; the next
            # listing (within a minute) shows the service connected.
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

    async def kits(self) -> list[Kit]:
        return sorted(
            (
                Kit.from_lmt(item)
                for item in await self._pages(KITS_PATH)
                if isinstance(item, dict) and item.get("id")
            ),
            key=lambda kit: kit.name.lower(),
        )

    async def profiles(self) -> list[Profile]:
        return sorted(
            (
                Profile(id=str(item["id"]), name=str(item.get("name") or item["id"]))
                for item in await self._pages(PROFILES_PATH)
                if isinstance(item, dict) and item.get("id")
            ),
            key=lambda profile: profile.name.lower(),
        )

    async def create_profile(self, name: str) -> Profile:
        response = await self._request("POST", PROFILES_PATH, body={"name": name})
        item = response.json()
        return Profile(id=str(item["id"]), name=str(item.get("name") or name))

    async def connect_for_bot(self, bot: BotTools, server_id: str) -> ConsentLink:
        """A link where this admin connects one kit service for the workspace bot."""
        if server_id not in (await bot.kit_tools()):
            raise ManagedToolsError("This tool kit has no such service")
        path = (
            f"{PROFILES_PATH}/{quote(bot.profile, safe='')}/servers/"
            f"{quote(server_id, safe='')}/credential-sessions"
        )
        if (link := _link((await self._request("POST", path)).json())) is None:
            raise ManagedToolsError("LangSmith did not return a consent link; retry")
        return link


class BotTools(ManagedTools):
    """A workspace's bot: its tool kit's tools, run as its LMT profile.

    Every run in the workspace loads them, public threads and automations included, so
    they run with credentials an admin connected for the whole workspace.
    """

    scope: MCPScope = "bot"

    def __init__(self, workspace: str, kit: str, profile: str) -> None:
        self.workspace = workspace.strip().lower()
        self.kit = lmt_id(kit)
        self.profile = lmt_id(profile, "bot profile")
        self.namespace = ("managed_tools", "bot", self.workspace, self.kit, self.profile)

    @classmethod
    async def for_workspace(cls, workspace: str) -> BotTools | None:
        """The workspace's bot, if an admin gave it both a tool kit and a profile."""
        from openswe.dashboard.workspace_settings_cache import cached_workspace_settings

        settings = await cached_workspace_settings(workspace)
        kit, profile = settings.managed_tools_gateway_id, settings.managed_tools_profile_id
        if kit is None or profile is None or not ENV.LANGSMITH_API_KEY.is_set():
            return None
        return cls(workspace, kit, profile)

    async def _authorize(self) -> None:
        """Every run in the workspace may call its bot's tools."""
        return

    async def headers(self) -> dict[str, str]:
        return {
            "X-Api-Key": ENV.LANGSMITH_API_KEY.get(),
            "X-LangSmith-Profile": self.profile,
            **self._tenant(),
        }

    @property
    def _kit_path(self) -> str:
        return f"{KITS_PATH}/{self.kit}"

    async def _load_detail(self) -> dict[str, object]:
        return (await self._request("GET", self._kit_path)).json()

    async def _detail(self) -> dict[str, object]:
        detail = await self._cached("kit")
        return detail if isinstance(detail, dict) else {}

    async def kit_tools(self) -> dict[str, dict[str, str]]:
        """The kit's tools by server: each server's upstream tool names, mapped to the
        names the kit lists them under."""
        grouped: dict[str, dict[str, str]] = {}
        tools = (await self._detail()).get("tools")
        for tool in tools if isinstance(tools, list) else []:
            if not isinstance(tool, dict):
                continue
            source = tool.get("server_id") or tool.get("builtin_server_key")
            upstream, listed = tool.get("tool_name"), tool.get("mcp_name")
            if isinstance(source, str) and isinstance(upstream, str) and isinstance(listed, str):
                grouped.setdefault(source, {})[upstream] = listed
        return grouped

    def _mcp_failure(self, response: httpx2.Response) -> ManagedToolsError:
        # A profile caller gets 409 where a person would get a consent link.
        return _ProfileMissing() if response.status_code == 409 else super()._mcp_failure(response)

    async def _load_usable(self) -> list[str]:
        async def listed(client: Client[StreamableHttpTransport]) -> list[str]:
            return [tool.name for tool in await client.list_tools()]

        try:
            return await self._mcp(f"{self._kit_path}/mcp", listed)
        except _ProfileMissing:
            # A kit requiring every connection lists nothing until all are made.
            return []

    async def _usable_tools(self) -> list[str]:
        """The kit tools the profile can call now: LMT lists only those."""
        usable = await self._cached("usable")
        return (
            [name for name in usable if isinstance(name, str)] if isinstance(usable, list) else []
        )

    async def _names(self) -> dict[str, str]:
        """Server names by ID, read with the bot's key; LangSmith may refuse them a key, so
        a name falls back to the one the kit's tool names start with."""
        try:
            return {server.id: server.name for server in await self.servers()}
        except ManagedToolsError:
            logger.info("Managed tools server names unavailable to the bot", exc_info=True)
            return {}

    @staticmethod
    def _fallback_name(server_id: str, tools: dict[str, str]) -> str:
        """The server part of the kit's tool names (``<server>_<tool>``)."""
        for upstream, listed in tools.items():
            if listed.endswith("_" + upstream):
                return listed.removesuffix("_" + upstream).replace("_", " ")
        return server_id

    def _loaders(self) -> dict[str, Callable[[], Awaitable[object]]]:
        return {**super()._loaders(), "kit": self._load_detail, "usable": self._load_usable}

    async def status(self) -> BotStatus:
        """Each kit server and whether the bot can use it, for the workspace's admins."""
        detail, kit_tools, usable, names = await asyncio.gather(
            self._detail(), self.kit_tools(), self._usable_tools(), self._names()
        )
        usable_names = set(usable)
        return BotStatus(
            kit=Kit.from_lmt({**detail, "id": self.kit}),
            profile_id=self.profile,
            servers=[
                BotServerStatus(
                    id=server_id,
                    name=names.get(server_id) or self._fallback_name(server_id, tools),
                    tool_count=len(tools),
                    connected=any(listed in usable_names for listed in tools.values()),
                )
                for server_id, tools in kit_tools.items()
            ],
        )

    async def _offers(self) -> list[_Offer]:
        kit_tools, usable, names = await asyncio.gather(
            self.kit_tools(), self._usable_tools(), self._names()
        )
        usable_names = set(usable)
        offers: list[_Offer] = []
        for server_id, tools in kit_tools.items():
            allowed = frozenset(
                upstream for upstream, listed in tools.items() if listed in usable_names
            )
            if allowed:
                name = names.get(server_id) or self._fallback_name(server_id, tools)
                offers.append(_Offer(ManagedServer(id=server_id, name=name), allowed))
        return offers


class _ProfileMissing(ManagedToolsError):
    """LMT's 409 to a profile caller: only an admin can connect a profile's services."""

    def __init__(self) -> None:
        super().__init__("An admin must connect this tool kit's services for the bot")
