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
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Literal
from urllib.parse import quote
from uuid import UUID

import httpx
from langchain_mcp_adapters.sessions import StreamableHttpConnection, create_session
from pydantic import BaseModel, Field

from mcp import ClientSession
from mcp.types import PaginatedRequestParams
from openswe.config import ENV
from openswe.credential_scope import private_credential_login
from openswe.dashboard.langsmith_oauth import (
    LangSmithOAuthError,
    langsmith_access_token,
    langsmith_issuer,
    langsmith_oauth_configured,
)
from openswe.mcp.models import MCPConnection
from openswe.mcp.runtime import MCPSource
from openswe.mcp.transport import mcp_http_client

logger = logging.getLogger(__name__)

GATEWAYS_PATH = "/v1/managed-tools/gateways"
CONSENT_SESSIONS_PATH = "/v1/agent-auth/oauth-authorization-sessions"
CONNECTION_NAME = "lmt"
_TIMEOUT_SECONDS = 30
_MAX_PAGES = 5
# LangSmith holds each status request open this long while consent is pending.
_CONSENT_WAIT_SECONDS = 25
# Consent links expire after ten minutes; stop waiting shortly after.
_CONSENT_DEADLINE_SECONDS = 11 * 60

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
    """LMT's 428: every service in the gateway this person still has to connect."""

    def __init__(self, missing: list[MissingCredential], links: dict[str, ConsentLink]) -> None:
        super().__init__("Connect every service in this gateway before using its tools")
        self.missing = missing
        self.links = links


class Gateway(BaseModel):
    id: str
    name: str
    tool_count: int


class GatewayStatus(BaseModel):
    """What one person sees for a gateway: ready, or what to connect first."""

    gateway: Gateway
    workspaces: list[str]
    ready: bool
    tool_count: int | None = None
    missing: list[MissingCredential] = Field(default_factory=list)


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


def _client() -> httpx.AsyncClient:
    return mcp_http_client(langsmith_issuer(), timeout=httpx.Timeout(_TIMEOUT_SECONDS))


def _credentials_required(body: object) -> GatewayCredentialsRequired:
    entries = body.get("credentials") if isinstance(body, dict) else None
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
    return GatewayCredentialsRequired(missing, links)


def _failure(response: httpx.Response) -> ManagedToolsError:
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
    return Gateway(
        id=str(item["id"]),
        name=str(item.get("name") or item["id"]),
        tool_count=len(tools) if isinstance(tools, list) else 0,
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


def _session_connection(headers: dict[str, str], gateway: str) -> StreamableHttpConnection:
    async def check(response: httpx.Response) -> None:
        if response.status_code >= 300:
            await response.aread()
            raise _failure(response)

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        client = mcp_http_client(_mcp_url(gateway), headers, timeout, auth)
        client.event_hooks["response"].append(check)
        return client

    return {
        "transport": "streamable_http",
        "url": _mcp_url(gateway),
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
    login: str, gateway: str, use: Callable[[ClientSession], Awaitable[T]]
) -> T:
    headers = await _headers(login)
    try:
        async with asyncio.timeout(_TIMEOUT_SECONDS):
            async with create_session(_session_connection(headers, gateway)) as session:
                await session.initialize()
                return await use(session)
    except Exception as exc:
        if (found := _wrapped(exc, ManagedToolsError)) is not None:
            raise found from None
        raise ManagedToolsError("Could not reach the managed tools gateway; retry later") from None


async def _tool_count(session: ClientSession) -> int:
    count = 0
    cursor: str | None = None
    for _ in range(_MAX_PAGES):
        page = await session.list_tools(
            params=PaginatedRequestParams(cursor=cursor) if cursor else None
        )
        count += len(page.tools)
        if not page.nextCursor or page.nextCursor == cursor:
            break
        cursor = page.nextCursor
    return count


async def gateway_status(login: str, gateway: str, workspaces: list[str]) -> GatewayStatus:
    """Whether ``login`` can use the gateway now, or which services to connect first."""
    summary = await get_gateway(login, gateway)
    try:
        count = await _with_session(login, gateway, _tool_count)
    except GatewayCredentialsRequired as required:
        return GatewayStatus(
            gateway=summary, workspaces=workspaces, ready=False, missing=required.missing
        )
    return GatewayStatus(gateway=summary, workspaces=workspaces, ready=True, tool_count=count)


async def connect_link(login: str, gateway: str, slug: str) -> ConsentLink | None:
    """A fresh consent link for one service in the gateway; None when it is connected."""
    try:
        await _with_session(login, gateway, _tool_count)
    except GatewayCredentialsRequired as required:
        if any(item.slug == slug and item.kind == "secret" for item in required.missing):
            raise ManagedToolsError(
                "Set this service's API key in LangSmith, then refresh"
            ) from None
        if slug in required.links:
            return required.links[slug]
        if any(item.slug == slug for item in required.missing):
            raise ManagedToolsError("LangSmith did not return a consent link; retry") from None
    return None


async def consent_outcome(login: str, auth_id: str) -> ConsentOutcome:
    """Wait for the person to finish (or abandon) one consent link."""
    headers = await _headers(login)
    url = f"{langsmith_issuer()}{CONSENT_SESSIONS_PATH}/{quote(auth_id, safe='')}"
    timeout = httpx.Timeout(_TIMEOUT_SECONDS + _CONSENT_WAIT_SECONDS)
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
