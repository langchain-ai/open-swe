"""LangSmith Managed Tools (LMT): each workspace's bot, and each person's own connections.

Every LMT server a run uses is one MCP connection to that server's own route
(``/v1/managed-tools/servers/{id}/mcp``), named after the server. A person's own
connection to a server therefore replaces the workspace bot's in their private threads,
by the runtime's usual precedence.

- The workspace bot is the workspace's LMT profile (``X-LangSmith-Profile``), called
  with Open SWE's own LangSmith API key: its runs load every server the profile has
  connected. Open SWE makes the profile the first time an admin connects a service for
  the bot; only admins connect a profile's services, in LangSmith or from the
  workspace's settings, where they can also register new servers.
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
from datetime import timedelta
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote
from uuid import UUID

import httpx2
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from pydantic import BaseModel, Field
from sqlalchemy import text

from openswe.config import ENV
from openswe.credential_scope import private_credential_login
from openswe.dashboard.langsmith_oauth import (
    LangSmithOAuthError,
    langsmith_access_token,
    langsmith_issuer,
    langsmith_oauth_configured,
)
from openswe.database.postgres import transaction
from openswe.mcp.models import MCPConnection, MCPScope
from openswe.mcp.runtime import MCPSource, refresh_mcp_catalog
from openswe.mcp.transport import mcp_http_client

if TYPE_CHECKING:
    from langgraph_api.cache import SWRResult

logger = logging.getLogger(__name__)

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


class BotStatus(BaseModel):
    """The servers a workspace's admins can connect for its bot, and which it has."""

    profile_id: str | None
    servers: list[ManagedServer]

    @property
    def connected(self) -> list[ManagedServer]:
        return [server for server in self.servers if server.connected]


class NewServer(BaseModel):
    """An MCP server an admin registers in LMT for the whole LangSmith workspace."""

    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=2048)
    kind: Literal["oauth", "secret"]
    header: str | None = Field(default=None, max_length=128)
    """For ``secret``: the header its API key goes in."""

    def lmt_body(self) -> dict[str, object]:
        if not self.url.startswith("https://"):
            raise ManagedToolsError("Use an HTTPS URL for the server")
        connection: dict[str, object] = {"type": self.kind}
        if self.kind == "secret":
            header = (self.header or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9-]{1,128}", header):
                raise ManagedToolsError("Name the header the API key goes in")
            connection["fields"] = [
                {"name": header.lower(), "injection": {"type": "header", "name": header}}
            ]
        return {
            "name": self.name.strip(),
            "upstream_url": self.url,
            "connection": connection,
            "scope": "workspace",
        }


def lmt_id(value: str, what: str) -> str:
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
    stored."""

    headers: dict[str, str] = Field(default_factory=dict, repr=False, exclude=True)

    def connection_headers(self) -> dict[str, str]:
        return self.headers

    def allows_tool(self, name: str) -> bool:
        return True

    @property
    def offers_tools(self) -> bool:
        return True


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

    async def server(self, server_id: str) -> ManagedServer:
        found = next((item for item in await self.servers() if item.id == server_id), None)
        if found is None:
            raise ManagedToolsError("LangSmith has no such service")
        return found

    async def offered(self) -> list[ManagedServer]:
        """The servers whose tools runs load: every one the caller connected an account to.
        Public servers count as connected for everyone, so they are no one's own."""
        return [
            server for server in await self.servers() if server.usable and server.has_credential
        ]

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
            offered = await self.offered()
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
        for server in offered:
            name = server.connection_name
            if name in connections:
                # LangSmith lets a registered server share a built-in's name (Linear, Pylon).
                name = f"{name}-{server.id[:8].lower()}"
            connections[name] = _ServerConnection(
                name=name,
                url=f"{langsmith_issuer()}{SERVERS_PATH}/{quote(server.id, safe='')}/mcp",
                revision=hashlib.sha256(server.id.encode()).hexdigest()[:16],
                updated_at="",
                headers=headers,
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

    async def bot_profile(self, bot: BotTools) -> BotTools:
        """``bot`` with an LMT profile, making and saving the workspace's first one.

        Under a lock per workspace, and reusing a profile LangSmith already has under the
        workspace's name, so admins connecting at once or a lost setting make no second one.
        """
        if bot.profile is not None:
            return bot
        async with transaction() as conn:
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock))"),
                {"lock": f"lmt-bot-profile:{bot.workspace}"},
            )
            if (current := await BotTools.of(bot.workspace)).profile:
                return current
            name = BotTools.profile_name(bot.workspace)
            existing = next(
                (
                    str(item["id"])
                    for item in await self._pages(PROFILES_PATH)
                    if isinstance(item, dict) and item.get("id") and item.get("name") == name
                ),
                None,
            )
            if existing is None:
                response = await self._request("POST", PROFILES_PATH, body={"name": name})
                existing = str(response.json()["id"])
            return await bot.adopt_profile(existing)

    async def create_server(self, new: NewServer) -> ManagedServer:
        """Register a server in LMT for the LangSmith workspace, as this admin."""
        response = await self._request("POST", SERVERS_PATH, body=new.lmt_body())
        if (server := ManagedServer.from_lmt(response.json())) is None:
            raise ManagedToolsError("LangSmith returned an unreadable server")
        await self.refresh()
        return server

    async def connect_for_bot(self, bot: BotTools, server_id: str) -> ConsentLink:
        """A link where this admin connects one service for the workspace bot."""
        path = f"{bot.profile_server_path(server_id)}/credential-sessions"
        if (link := _link((await self._request("POST", path)).json())) is None:
            raise ManagedToolsError("LangSmith did not return a consent link; retry")
        return link

    async def disconnect_for_bot(self, bot: BotTools, server_id: str) -> None:
        """Delete the workspace bot's credential for one service; LangSmith keeps the server."""
        response = await self._request(
            "DELETE", f"{bot.profile_server_path(server_id)}/credential", ok=(404,)
        )
        if response.status_code == 404:
            raise ManagedToolsError("The bot has no connection to this service")
        await bot.refresh()


class BotTools(ManagedTools):
    """A workspace's bot: every server its LMT profile has connected.

    Every run in the workspace loads them, public threads and automations included, so
    they run with credentials an admin connected for the whole workspace.
    """

    scope: MCPScope = "bot"

    def __init__(self, workspace: str, profile: str | None = None) -> None:
        self.workspace = workspace.strip().lower()
        # None until an admin first connects a service for the bot.
        self.profile = None if profile is None else lmt_id(profile, "bot profile")
        self.namespace = ("managed_tools", "bot", self.workspace, self.profile or "")

    @staticmethod
    def profile_name(workspace: str) -> str:
        return f"Open SWE bot ({workspace})"

    @classmethod
    async def of(cls, workspace: str) -> BotTools:
        """The workspace's bot, with the profile it has so far."""
        from openswe.dashboard.workspace_settings_cache import cached_workspace_settings

        settings = await cached_workspace_settings(workspace)
        return cls(workspace, settings.managed_tools_profile_id)

    @classmethod
    async def for_workspace(cls, workspace: str) -> BotTools | None:
        """The bot a run loads, once it has a profile and Open SWE has a LangSmith key."""
        bot = await cls.of(workspace)
        return bot if bot.profile is not None and ENV.LANGSMITH_API_KEY.is_set() else None

    async def adopt_profile(self, profile: str) -> BotTools:
        """Save ``profile`` as the workspace bot's, returning the bot that runs as it."""
        from openswe.dashboard.workspace_settings import (
            WorkspaceSettingsUpdate,
            upsert_workspace_overrides,
            workspace_record,
        )

        record = {**await workspace_record(self.workspace), "managed_tools_profile_id": profile}
        await upsert_workspace_overrides(
            self.workspace, WorkspaceSettingsUpdate.model_validate(record)
        )
        return BotTools(self.workspace, profile)

    def profile_server_path(self, server_id: str) -> str:
        if self.profile is None:
            raise ManagedToolsError("This workspace bot has no LMT profile yet")
        profile, server = quote(self.profile, safe=""), quote(server_id, safe="")
        return f"{PROFILES_PATH}/{profile}/servers/{server}"

    async def _authorize(self) -> None:
        """Every run in the workspace may call its bot's tools."""
        return

    async def headers(self) -> dict[str, str]:
        if not ENV.LANGSMITH_API_KEY.is_set():
            raise ManagedToolsError("Open SWE has no LangSmith API key for the workspace bot")
        return {
            "X-Api-Key": ENV.LANGSMITH_API_KEY.get(),
            **({"X-LangSmith-Profile": self.profile} if self.profile else {}),
            **self._tenant(),
        }

    async def offered(self) -> list[ManagedServer]:
        return [] if self.profile is None else await super().offered()

    async def status(self) -> BotStatus:
        """Every server an admin could connect for the bot, and whether it has."""
        servers = [server for server in await self.servers() if server.kind != "none"]
        return BotStatus(
            profile_id=self.profile,
            servers=[
                # Without a profile, the key reports no one's credentials.
                server.model_copy(
                    update={
                        "connected": self.profile is not None
                        and server.usable
                        and server.has_credential
                    }
                )
                for server in servers
            ],
        )
