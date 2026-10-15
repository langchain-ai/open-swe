"""Durable personal OAuth lifecycle around the MCP SDK's protocol engine."""

import asyncio
import hashlib
import logging
import secrets
import time
from collections.abc import AsyncGenerator
from contextvars import ContextVar
from datetime import UTC, datetime
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid7

import anyio
import httpx2
from pydantic import AnyUrl, BaseModel, ConfigDict
from sqlalchemy import ColumnElement, ForeignKey, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from mcp.client.auth import OAuthClientProvider
from mcp.client.auth.oauth2 import PKCEParameters
from mcp.client.auth.utils import validate_authorization_response_iss
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)
from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.encryption import decrypt_token, encrypt_token
from openswe.mcp.models import MCPConnection, MCPOAuthStatus, https_mcp_url
from openswe.mcp.oauth import MCPOAuthError
from openswe.mcp.transport import MCPDiscoveryError, mcp_http_client
from openswe.users.records import UnknownUser, user_id_for_login
from openswe.utils.dashboard_links import dashboard_api_base_url
from openswe.utils.url_safety import resolve_and_validate

_sdk_active: ContextVar[bool] = ContextVar("personal_mcp_oauth_sdk_active", default=False)


class _RedactSDKLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if _sdk_active.get():
            record.msg = "Personal MCP OAuth protocol event"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
        return True


logging.getLogger("mcp.client.auth.oauth2").addFilter(_RedactSDKLogs())

logger = logging.getLogger(__name__)
CALLBACK_PATH = "/dashboard/api/my-mcps/oauth/callback"
_MAX_RESPONSE_BYTES = 1024 * 1024
_RECONNECT = "Reconnect this personal MCP in Settings → Connections"


class PersonalMCPOAuthRow(Base):
    __tablename__ = "personal_mcp_oauth"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str]
    sealed_authorization: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )


class Authorization(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    identity: str
    generation: str = ""
    status: MCPOAuthStatus
    revision: str = ""
    client_metadata: OAuthClientMetadata
    client_info: OAuthClientInformationFull | None = None
    metadata: OAuthMetadata | None = None
    resource_metadata: ProtectedResourceMetadata | None = None
    tokens: OAuthToken | None = None
    expires_at: float | None = None
    state: str | None = None
    verifier: str | None = None
    session_binding: str = ""
    pending_until: float = 0
    refreshing_until: float = 0
    retry_after: float = 0

    @classmethod
    def new(cls, record: MCPConnection) -> Authorization:
        callback = f"{dashboard_api_base_url()}{CALLBACK_PATH}"
        parsed = urlsplit(callback)
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
            raise MCPOAuthError("Interactive MCP OAuth requires an HTTPS dashboard API URL")
        return cls(
            identity=authorization_identity(record),
            generation=secrets.token_hex(16),
            revision=record.revision,
            status=MCPOAuthStatus(status="not_connected"),
            client_metadata=OAuthClientMetadata(
                client_name="Open SWE",
                redirect_uris=[AnyUrl(callback)],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                token_endpoint_auth_method="none",
            ),
        )


def authorization_identity(record: MCPConnection) -> str:
    return hashlib.sha256(
        f"{record.url}\n{record.transport}\n{record.auth}\n{record.encrypted_headers}".encode()
    ).hexdigest()


class AuthorizationStore:
    def __init__(self, login: str, name: str) -> None:
        self.login = login.strip().lower()
        self.name = name

    def where(self) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
        return (
            PersonalMCPOAuthRow.user_id == user_id_for_login(self.login),
            PersonalMCPOAuthRow.name == self.name,
        )

    @staticmethod
    def unseal(row: PersonalMCPOAuthRow) -> Authorization:
        return Authorization.model_validate_json(decrypt_token(row.sealed_authorization) or "")

    @staticmethod
    def seal(row: PersonalMCPOAuthRow, authorization: Authorization) -> None:
        row.sealed_authorization = encrypt_token(authorization.model_dump_json())
        row.updated_at = datetime.now(UTC)
        row.updated_by = row.user_id

    async def get(self) -> Authorization | None:
        async with postgres.session() as session:
            row = await session.scalar(select(PersonalMCPOAuthRow).where(*self.where()))
            return None if row is None else self.unseal(row)

    async def put(self, authorization: Authorization) -> None:
        async with postgres.session() as session:
            owner = await session.scalar(select(user_id_for_login(self.login)))
            if owner is None:
                raise UnknownUser(self.login)
            statement = insert(PersonalMCPOAuthRow).values(
                id=uuid7(),
                user_id=owner,
                name=self.name,
                sealed_authorization=encrypt_token(authorization.model_dump_json()),
                created_by=owner,
                updated_by=owner,
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[PersonalMCPOAuthRow.user_id, PersonalMCPOAuthRow.name],
                    set_={
                        "sealed_authorization": statement.excluded.sealed_authorization,
                        "updated_at": func.clock_timestamp(),
                        "updated_by": owner,
                    },
                )
            )

    async def delete(self) -> None:
        async with postgres.session() as session:
            await session.execute(delete(PersonalMCPOAuthRow).where(*self.where()))

    async def finish(self, authorization: Authorization) -> None:
        async with postgres.session() as session:
            row = await session.scalar(
                select(PersonalMCPOAuthRow).where(*self.where()).with_for_update()
            )
            if row is None or self.unseal(row).generation != authorization.generation:
                raise MCPOAuthError("This MCP authorization was disconnected or replaced")
            self.seal(row, authorization)

    async def consume(self, record: MCPConnection, state: str, binding: str) -> Authorization:
        async with postgres.session() as session:
            row = await session.scalar(
                select(PersonalMCPOAuthRow).where(*self.where()).with_for_update()
            )
            authorization = None if row is None else self.unseal(row)
            if (
                row is None
                or authorization is None
                or authorization.identity != authorization_identity(record)
                or authorization.revision != record.revision
                or authorization.pending_until <= time.time()
                or not authorization.state
                or not secrets.compare_digest(authorization.state, state)
                or not secrets.compare_digest(authorization.session_binding, binding)
            ):
                raise MCPOAuthError("Invalid or expired MCP OAuth callback; start Connect again")
            authorization.state = None
            authorization.status = MCPOAuthStatus(
                status="failed", error="Authorization was interrupted; start Connect again"
            )
            authorization.pending_until = 0
            self.seal(row, authorization)
            return authorization

    async def reject(self, access_token: str) -> None:
        async with postgres.session() as session:
            row = await session.scalar(
                select(PersonalMCPOAuthRow).where(*self.where()).with_for_update()
            )
            if row is not None:
                authorization = self.unseal(row)
                if authorization.tokens and authorization.tokens.access_token == access_token:
                    authorization.tokens = None
                    authorization.status = MCPOAuthStatus(
                        status="reconnect_required", error=_RECONNECT
                    )
                    self.seal(row, authorization)

    async def refresh_claim(
        self, record: MCPConnection, rejected: str | None
    ) -> tuple[Authorization, bool]:
        async with postgres.session() as session:
            row = await session.scalar(
                select(PersonalMCPOAuthRow).where(*self.where()).with_for_update()
            )
            authorization = None if row is None else self.unseal(row)
            if (
                row is None
                or authorization is None
                or authorization.identity != authorization_identity(record)
            ):
                raise MCPOAuthError(_RECONNECT)
            now = time.time()
            if authorization.refreshing_until:
                if authorization.refreshing_until > now:
                    return authorization, False
                authorization.status = MCPOAuthStatus(
                    status="reconnect_required",
                    error="The last refresh was interrupted. " + _RECONNECT,
                )
                authorization.tokens = None
                self.seal(row, authorization)
                return authorization, False
            if authorization.status.status == "failed" and authorization.tokens is not None:
                if authorization.retry_after > now:
                    raise MCPOAuthError(
                        authorization.status.error or "OAuth provider temporarily unavailable"
                    )
                authorization.status = MCPOAuthStatus(status="connected")
            if authorization.status.status != "connected" or authorization.tokens is None:
                raise MCPOAuthError(_RECONNECT)
            if authorization.tokens.access_token != rejected and (
                authorization.expires_at is None or authorization.expires_at > now + 30
            ):
                return authorization, False
            if not authorization.tokens.refresh_token:
                authorization.status = MCPOAuthStatus(status="reconnect_required", error=_RECONNECT)
                self.seal(row, authorization)
                return authorization, False
            authorization.refreshing_until = now + 60
            self.seal(row, authorization)
            return authorization, True


class SDKStorage:
    def __init__(self, authorization: Authorization) -> None:
        self.authorization = authorization

    async def get_tokens(self) -> OAuthToken | None:
        return self.authorization.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        if (
            not tokens.access_token
            or any(ord(char) < 33 or ord(char) > 126 for char in tokens.access_token)
            or tokens.expires_in is not None
            and tokens.expires_in <= 0
        ):
            raise MCPOAuthError("The MCP authorization server returned an invalid token")
        self.authorization.tokens = tokens
        self.authorization.expires_at = (
            None if tokens.expires_in is None else time.time() + tokens.expires_in
        )

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.authorization.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.authorization.client_info = client_info


class RefreshUnavailable(MCPOAuthError):
    """A transient provider failure, not evidence that consent was revoked."""


class ConsentReady(Exception):
    def __init__(self, url: str) -> None:
        self.url = url


class HostedProvider(OAuthClientProvider):
    def __init__(self, record: MCPConnection, authorization: Authorization) -> None:
        self.authorization = authorization
        self.connection_name = record.name
        super().__init__(record.url, authorization.client_metadata, SDKStorage(authorization))
        self.context.client_info = authorization.client_info
        self.context.oauth_metadata = authorization.metadata
        self.context.protected_resource_metadata = authorization.resource_metadata
        self.context.auth_server_url = (
            str(authorization.metadata.issuer) if authorization.metadata else None
        )
        self.context.current_tokens = authorization.tokens
        self.context.token_expiry_time = authorization.expires_at

    async def _perform_authorization_code_grant(self) -> tuple[str, str]:
        metadata = self.context.oauth_metadata
        client = self.context.client_info
        if (
            metadata is None
            or client is None
            or metadata.authorization_endpoint is None
            or not self.context.client_metadata.redirect_uris
        ):
            raise MCPOAuthError(
                "This MCP must advertise OAuth discovery and dynamic client registration"
            )
        endpoint = https_mcp_url(str(metadata.authorization_endpoint))
        await asyncio.to_thread(resolve_and_validate, endpoint)
        pkce = PKCEParameters.generate()
        state = f"{self.connection_name}.{secrets.token_urlsafe(32)}"
        self.authorization.state = state
        self.authorization.verifier = pkce.code_verifier
        self.authorization.pending_until = time.time() + 600
        self.authorization.metadata = metadata
        self.authorization.resource_metadata = self.context.protected_resource_metadata
        self.authorization.client_metadata = self.context.client_metadata
        parameters = {
            "response_type": "code",
            "client_id": client.client_id,
            "redirect_uri": str(self.context.client_metadata.redirect_uris[0]),
            "state": state,
            "code_challenge": pkce.code_challenge,
            "code_challenge_method": "S256",
            "resource": self.context.get_resource_url(),
        }
        if self.context.client_metadata.scope:
            parameters["scope"] = self.context.client_metadata.scope
            if "offline_access" in self.context.client_metadata.scope.split():
                parameters["prompt"] = "consent"
        separator = "&" if "?" in str(metadata.authorization_endpoint) else "?"
        raise ConsentReady(f"{metadata.authorization_endpoint}{separator}{urlencode(parameters)}")

    async def guarded_request(self, request: httpx2.Request) -> httpx2.Response:
        url = https_mcp_url(str(request.url))
        async with asyncio.timeout(30), mcp_http_client(url) as client:
            async with client.stream(
                request.method, url, headers=request.headers, content=request.content
            ) as response:
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > _MAX_RESPONSE_BYTES:
                        raise MCPOAuthError("MCP OAuth response exceeded the size limit")
                headers = response.headers.copy()
                headers.pop("content-encoding", None)
                headers.pop("content-length", None)
                return httpx2.Response(
                    response.status_code,
                    headers=headers,
                    content=bytes(chunks),
                    request=request,
                )

    async def oauth_request(self, request: httpx2.Request) -> httpx2.Response:
        url = https_mcp_url(str(request.url))
        metadata = self.context.oauth_metadata
        if request.method == "GET":
            if "authorization" in request.headers or request.content:
                raise MCPOAuthError("OAuth discovery must not carry credentials")
        elif request.method == "POST":
            endpoints = (
                set()
                if metadata is None
                else {str(metadata.registration_endpoint), str(metadata.token_endpoint)}
            )
            if url not in endpoints:
                raise MCPOAuthError(
                    "OAuth credentials may only reach the discovered authorization server endpoints"
                )
        else:
            raise MCPOAuthError("Unsupported OAuth request")
        return await self.guarded_request(request)

    async def begin(self, headers: dict[str, str]) -> str:
        request = httpx2.Request(
            "POST",
            self.context.server_url,
            headers={
                **headers,
                "Content-Type": "application/json",
                "MCP-Protocol-Version": "2025-11-25",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "Open SWE", "version": "1"},
                },
            },
        )
        flow = super().async_auth_flow(request)
        token = _sdk_active.set(True)
        try:
            outgoing = await anext(flow)
            while True:
                if outgoing is request:
                    response = await self.guarded_request(request)
                    if response.status_code not in {401, 403}:
                        raise MCPOAuthError(
                            "This MCP did not request OAuth authorization; check its URL and authentication mode"
                        )
                else:
                    response = await self.oauth_request(outgoing)
                outgoing = await flow.asend(response)
        except ConsentReady as consent:
            return consent.url
        finally:
            await flow.aclose()
            _sdk_active.reset(token)

    async def complete(self, code: str, issuer: str | None) -> None:
        validate_authorization_response_iss(issuer, self.context.oauth_metadata)
        if not self.authorization.verifier:
            raise MCPOAuthError("The MCP authorization transaction has expired")
        request = await self._exchange_token_authorization_code(code, self.authorization.verifier)
        response = await self.oauth_request(request)
        await self._handle_token_response(response)
        self.authorization.verifier = None

    async def refresh(self) -> None:
        token = _sdk_active.set(True)
        try:
            try:
                response = await self.oauth_request(await self._refresh_token())
            except httpx2.ConnectError, httpx2.ConnectTimeout, MCPDiscoveryError:
                raise RefreshUnavailable(
                    "The MCP authorization server could not be reached; retry shortly"
                ) from None
            if response.status_code >= 500 or response.status_code == 429:
                raise RefreshUnavailable(
                    "The MCP authorization server is temporarily unavailable; retry shortly"
                )
            if not await self._handle_refresh_response(response):
                raise MCPOAuthError(_RECONNECT)
        finally:
            _sdk_active.reset(token)


async def oauth_status(login: str, record: MCPConnection) -> MCPOAuthStatus:
    authorization = await AuthorizationStore(login, record.name).get()
    if authorization is None or authorization.identity != authorization_identity(record):
        return MCPOAuthStatus(status="not_connected")
    if authorization.status.status == "connecting" and authorization.pending_until <= time.time():
        return MCPOAuthStatus(status="not_connected", error="Connect expired; try again")
    return authorization.status


async def start_oauth(login: str, record: MCPConnection, session_binding: str) -> str:
    if record.auth != "oauth":
        raise MCPOAuthError("Save this personal MCP with interactive OAuth before connecting")
    if urlsplit(record.url).hostname == "mcp.slack.com":
        raise MCPOAuthError(
            "Slack MCP requires an Open SWE hosted OAuth client with this callback registered. Ask your administrator to configure one; the CLI's localhost client cannot be reused"
        )
    authorization = Authorization.new(record)
    authorization.session_binding = session_binding
    try:
        async with asyncio.timeout(90):
            url = await HostedProvider(record, authorization).begin(record.connection_headers())
    except Exception:
        logger.warning("Personal MCP OAuth discovery failed", extra={"mcp_name": record.name})
        raise MCPOAuthError(
            "Could not start MCP OAuth. Check the server URL and its support for dynamic registration with a hosted callback"
        ) from None
    authorization.status = MCPOAuthStatus(status="connecting")
    await AuthorizationStore(login, record.name).put(authorization)
    return url


async def complete_oauth(
    login: str, record: MCPConnection, state: str, code: str, binding: str, issuer: str | None
) -> None:
    store = AuthorizationStore(login, record.name)
    authorization = await store.consume(record, state, binding)
    try:
        await HostedProvider(record, authorization).complete(code, issuer)
        authorization.status = MCPOAuthStatus(status="connected")
    except Exception:
        logger.warning("Personal MCP OAuth callback failed", extra={"mcp_name": record.name})
        authorization.status = MCPOAuthStatus(
            status="failed", error="Authorization failed; start Connect again"
        )
        raise MCPOAuthError("MCP authorization failed; start Connect again") from None
    finally:
        authorization.verifier = None
        with anyio.CancelScope(shield=True):
            await store.finish(authorization)


class PersonalOAuthAuth(httpx2.Auth):
    requires_request_body = True

    def __init__(self, login: str, record: MCPConnection) -> None:
        self.login = login
        self.record = record

    async def access_token(self, rejected: str | None = None) -> str:
        store = AuthorizationStore(self.login, self.record.name)
        for _ in range(100):
            authorization, claimed = await store.refresh_claim(self.record, rejected)
            if claimed:
                with anyio.CancelScope(shield=True):
                    try:
                        await HostedProvider(self.record, authorization).refresh()
                    except RefreshUnavailable as exc:
                        logger.warning(
                            "Personal MCP OAuth provider unavailable",
                            extra={"mcp_name": self.record.name},
                        )
                        authorization.status = MCPOAuthStatus(status="failed", error=str(exc))
                        authorization.retry_after = time.time() + 30
                    except Exception:
                        logger.warning(
                            "Personal MCP OAuth refresh failed",
                            extra={"mcp_name": self.record.name},
                        )
                        authorization.status = MCPOAuthStatus(
                            status="reconnect_required", error=_RECONNECT
                        )
                        authorization.tokens = None
                    finally:
                        authorization.refreshing_until = 0
                        await store.finish(authorization)
            elif authorization.refreshing_until > time.time():
                await asyncio.sleep(0.1)
                continue
            if authorization.status.status != "connected" or authorization.tokens is None:
                raise MCPOAuthError(authorization.status.error or _RECONNECT)
            return authorization.tokens.access_token
        raise MCPOAuthError("MCP authorization is refreshing; try again shortly")

    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        token = await self.access_token()
        request.headers["Authorization"] = f"Bearer {token}"
        response = yield request
        if response.status_code == 401:
            await response.aread()
            token = await self.access_token(token)
            request.headers["Authorization"] = f"Bearer {token}"
            response = yield request
            if response.status_code == 401:
                await AuthorizationStore(self.login, self.record.name).reject(token)
