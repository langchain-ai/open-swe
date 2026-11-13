"""LangSmith Managed Tools (LMT): the instance's two tool kits, read from LangSmith.

Every run loads one MCP connection, ``lmt``, to a kit's route
(``/v1/managed-tools/kits/{id}/mcp``). A private thread loads the personal kit
``LMT_KIT_ID``, called with the owner's own LangSmith sign-in; it lists every tool in the
kit, including those of services the owner has not connected yet, and calling one of
those asks them to connect it. Every other run loads the bot's kit ``LMT_BOT_KIT_ID``,
called as the LMT profile ``LMT_BOT_PROFILE_ID`` with Robin's LangSmith key, and loads
only the tools of services a workspace admin connected for that profile. Two kits let a
service sign people in one way (OAuth) and the bot another (an API key), since an LMT
server has one sign-in type.

Robin only reads LMT; the kits, their servers and the profile's credentials are managed
in LangSmith, and a connect card links a person to LangSmith's own consent or API key
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
from openswe.mcp.models import MCPConnection, MCPScope
from openswe.mcp.runtime import MCPSource, refresh_mcp_catalog
from openswe.mcp.transport import MCPCredentialMissing, mcp_http_client

if TYPE_CHECKING:
    from langgraph_api.cache import SWRResult

logger = logging.getLogger(__name__)

SERVERS_PATH = "/v1/managed-tools/servers"
KITS_PATH = "/v1/managed-tools/kits"
KIT_CONNECTION = "lmt"
CONSENT_SESSIONS_PATH = "/v1/agent-auth/oauth-authorization-sessions"
_MISSING_CREDENTIALS_META = "com.langchain.langsmith/missingCredentials"
_TIMEOUT_SECONDS = 30
_MAX_PAGES = 5
_LISTING_FRESH_FOR = timedelta(minutes=1)
_LISTING_MAX_AGE = timedelta(minutes=10)
# LangSmith holds each status request open this long while consent is pending.
_CONSENT_WAIT_SECONDS = 25

type CredentialKind = Literal["none", "oauth", "secret"]
type ConsentStatus = Literal["pending", "completed", "failed", "expired"]


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
    personal: bool = False
    """The caller registered it for themselves; nobody else sees it."""

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
            personal=item.get("scope") == "personal",
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


def managed_tools_configured() -> bool:
    """Whether people load the personal kit with their own LangSmith sign-in."""
    return langsmith_oauth_configured() and bool(ENV.LMT_KIT_ID.optional())


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


class _KitConnection(MCPConnection):
    """The kit's route, carrying the caller's current credential in memory, never stored."""

    headers: dict[str, str] = Field(default_factory=dict, repr=False, exclude=True)
    connected_only: bool = False
    """Leave out the tools of services the caller has no credential for, which nobody
    running as the caller could connect."""

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

    def unusable_tools(self, meta: object) -> set[str]:
        if not self.connected_only or not isinstance(meta, dict):
            return set()
        entries = meta.get(_MISSING_CREDENTIALS_META)
        return {
            tool["name"]
            for entry in (entries if isinstance(entries, list) else [])
            if isinstance(entry, dict)
            for tool in (entry.get("tools") if isinstance(entry.get("tools"), list) else [])
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        }


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
        return {"servers": self._load_servers, "kit": self._load_kit_servers}

    async def _load_kit_servers(self) -> list[str]:
        """The IDs (or built-in keys) of the servers the kit links tools from."""
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

    async def kit_servers(self) -> list[ManagedServer]:
        """The servers whose tools the kit offers, with the caller's state of each."""
        cached = await self._cached("kit")
        linked = set(cached) if isinstance(cached, list) else set()
        return [server for server in await self.servers() if server.id in linked]

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

    async def server(self, server_id: str) -> ManagedServer:
        found = next((item for item in await self.servers() if item.id == server_id), None)
        if found is None:
            raise ManagedToolsError("LangSmith has no such service")
        return found

    async def refresh(self) -> None:
        """Re-read the listings now, then the kit's tools, so a service that was just
        connected or disconnected shows in the next run."""
        for listing in self._loaders():
            result = await self._swr(listing)
            if result.status != "miss":
                await result.mutate()
        source = self.source()
        for connection in await self.connections():
            await refresh_mcp_catalog(source, connection.name)

    async def connections(self) -> list[MCPConnection]:
        """Resolved on every lookup, so each tool call carries a fresh credential.

        Failures leave out only the kit: raising would make the runtime drop every
        tier's MCPs, and only the kit depends on LangSmith.
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
        return [
            _KitConnection(
                name=KIT_CONNECTION,
                url=f"{langsmith_issuer()}{KITS_PATH}/{quote(kit, safe='')}/mcp",
                revision=revision,
                updated_at="",
                headers=headers,
                connected_only=self.connected_only,
            )
        ]

    async def revision(self, kit: str) -> str:
        """What the kit's cached tool list is kept under; a new one lists its tools again."""
        return hashlib.sha256(kit.encode()).hexdigest()[:16]

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

    Their private threads load every tool in the kit; calling one of a service they have
    not connected yet asks them to connect it.
    """

    scope: MCPScope = "managed"

    def __init__(self, login: str) -> None:
        self.login = login.strip().lower()
        self.namespace = ("managed_tools", self.login)

    def configured(self) -> bool:
        return managed_tools_configured()

    def kit(self) -> str | None:
        return ENV.LMT_KIT_ID.optional() or None

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

    async def revision(self, kit: str) -> str:
        """The kit as this person has connected it: lists only a connected service's tools
        when LMT knows them from the service itself, so a run after a connection or
        disconnection, made here or in LangSmith, lists the kit's tools again."""
        try:
            servers = await self.kit_servers()
        except ManagedToolsError:
            logger.warning("Managed tools kit state unavailable", exc_info=True)
            return await super().revision(kit)
        connected = sorted(server.id for server in servers if server.usable)
        return hashlib.sha256(json.dumps([kit, connected]).encode()).hexdigest()[:16]

    async def connectable(self) -> list[str]:
        """The names of the kit's services this person could connect, for the prompt; empty
        when LangSmith cannot say, since the run goes on without them."""
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
