"""LangSmith Managed Tools (LMT) gateways, used by each person with their own grants.

A workspace admin picks one LMT gateway (a curated set of tools across MCP servers,
served from one MCP URL). Private threads in that workspace load the gateway with the
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

GATEWAYS_PATH = "/v1/managed-tools/gateways"
CONSENT_SESSIONS_PATH = "/v1/agent-auth/oauth-authorization-sessions"
CONNECTION_NAME = "lmt"
_MISSING_CREDENTIALS_META = "com.langchain.langsmith/missingCredentials"
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
        super().__init__("Connect every service in this gateway before using its tools")
        self.missing = missing
        self.links = links


class Gateway(BaseModel):
    id: str
    name: str
    tool_count: int


class GatewayStatus(BaseModel):
    """What one person sees for a gateway: every service connected, or what is left."""

    gateway: Gateway
    workspaces: list[str]
    ready: bool
    tool_count: int | None = None
    missing: list[MissingCredential] = Field(default_factory=list)
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


def managed_tools_configured() -> bool:
    return langsmith_oauth_configured()


def gateway_id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        raise ManagedToolsError("Unknown managed tools gateway") from None


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
        slug = entry["slug"]
        missing.append(
            MissingCredential(
                slug=slug, display_name=str(entry.get("display_name") or slug), kind=kind
            )
        )
        url = entry.get("verification_url")
        if kind == "oauth" and isinstance(url, str) and url.startswith("https://"):
            auth_id = entry.get("auth_id")
            links[slug] = ConsentLink(
                url=url, auth_id=auth_id if isinstance(auth_id, str) else None
            )
    return missing, links


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
        return ManagedToolsError("LangSmith refused access to this gateway")
    if response.status_code == 404:
        return ManagedToolsError("The managed tools gateway no longer exists")
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
                langsmith_issuer() + GATEWAYS_PATH, headers=headers, params=params
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


async def get_gateway(login: str, gateway: str) -> Gateway:
    headers = await _headers(login)
    async with _client() as client:
        response = await client.get(
            f"{langsmith_issuer()}{GATEWAYS_PATH}/{gateway_id(gateway)}", headers=headers
        )
    if not response.is_success:
        raise _failure(response)
    return _gateway(response.json())


def _mcp_url(gateway: str) -> str:
    return f"{langsmith_issuer()}{GATEWAYS_PATH}/{gateway_id(gateway)}/mcp"


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
        raise ManagedToolsError("Could not reach the managed tools gateway; retry later") from None


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
    return _Listing(len(result.tools), missing, links, all_required=False)


async def gateway_status(login: str, gateway: str, workspaces: list[str]) -> GatewayStatus:
    """Whether ``login`` has connected every service in the gateway, or which are left."""
    summary = await get_gateway(login, gateway)
    listing = await _listing(login, gateway)
    return GatewayStatus(
        gateway=summary,
        workspaces=workspaces,
        ready=not listing.missing,
        tool_count=listing.tool_count,
        missing=listing.missing,
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
        namespace=("managed_tools", login, gateway_id(gateway)),
        list_connections=partial(_connections, login, gateway),
        get_connection=partial(_connection, login, gateway),
        authorize=authorize,
    )
