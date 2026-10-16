"""Durable personal OAuth lifecycle around `langchain.mcp.oauth`."""

import asyncio
import hashlib
import logging
import secrets
import time
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID, uuid7

import anyio
import httpx2
from langchain.mcp.oauth import (
    InMemoryOAuthCredentialStore,
    MCPOAuthProvider,
    MCPOAuthUnavailableError,
    OAuthCredentials,
    PendingAuthorization,
)
from langchain.mcp.oauth import MCPOAuthError as SharedOAuthError
from pydantic import AnyUrl, BaseModel, ConfigDict, Field
from sqlalchemy import ColumnElement, ForeignKey, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from mcp.shared.auth import OAuthClientMetadata
from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.encryption import decrypt_token, encrypt_token
from openswe.mcp.models import MCPConnection, MCPOAuthStatus, https_mcp_url
from openswe.mcp.oauth import MCPOAuthError
from openswe.mcp.transport import MCPDiscoveryError, mcp_http_client
from openswe.users.records import UnknownUser, user_id_for_login
from openswe.utils.dashboard_links import dashboard_api_base_url
from openswe.utils.url_safety import resolve_and_validate

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
    credentials: OAuthCredentials = Field(default_factory=OAuthCredentials)
    pending: PendingAuthorization | None = None
    session_binding: str = ""
    refreshing_until: float = 0
    retry_after: float = 0

    @classmethod
    def new(cls, record: MCPConnection) -> Authorization:
        return cls(
            identity=authorization_identity(record),
            generation=secrets.token_hex(16),
            revision=record.revision,
            status=MCPOAuthStatus(status="not_connected"),
        )

    def require_reconnect(self, error: str = _RECONNECT) -> None:
        self.status = MCPOAuthStatus(status="reconnect_required", error=error)
        self.credentials = self.credentials.model_copy(update={"tokens": None, "expires_at": None})


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

    async def consume(
        self, record: MCPConnection, state: str, binding: str
    ) -> tuple[Authorization, PendingAuthorization]:
        """Claim the pending login for a callback, so it can complete at most once."""
        async with postgres.session() as session:
            row = await session.scalar(
                select(PersonalMCPOAuthRow).where(*self.where()).with_for_update()
            )
            authorization = None if row is None else self.unseal(row)
            pending = None if authorization is None else authorization.pending
            if (
                row is None
                or authorization is None
                or pending is None
                or authorization.identity != authorization_identity(record)
                or authorization.revision != record.revision
                or pending.expires_at <= time.time()
                or not secrets.compare_digest(pending.state, state)
                or not secrets.compare_digest(authorization.session_binding, binding)
            ):
                raise MCPOAuthError("Invalid or expired MCP OAuth callback; start Connect again")
            authorization.pending = None
            authorization.status = MCPOAuthStatus(
                status="failed", error="Authorization was interrupted; start Connect again"
            )
            self.seal(row, authorization)
            return authorization, pending

    async def reject(self, access_token: str) -> None:
        async with postgres.session() as session:
            row = await session.scalar(
                select(PersonalMCPOAuthRow).where(*self.where()).with_for_update()
            )
            if row is not None:
                authorization = self.unseal(row)
                tokens = authorization.credentials.tokens
                if tokens and tokens.access_token == access_token:
                    authorization.require_reconnect()
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
            credentials = authorization.credentials
            if authorization.refreshing_until:
                if authorization.refreshing_until > now:
                    return authorization, False
                authorization.require_reconnect("The last refresh was interrupted. " + _RECONNECT)
                self.seal(row, authorization)
                return authorization, False
            if authorization.status.status == "failed" and credentials.tokens is not None:
                if authorization.retry_after > now:
                    raise MCPOAuthError(
                        authorization.status.error or "OAuth provider temporarily unavailable"
                    )
                authorization.status = MCPOAuthStatus(status="connected")
            if authorization.status.status != "connected" or credentials.tokens is None:
                raise MCPOAuthError(_RECONNECT)
            if credentials.tokens.access_token != rejected and (
                credentials.expires_at is None or credentials.expires_at > now + 30
            ):
                return authorization, False
            if not credentials.tokens.refresh_token:
                authorization.require_reconnect()
                self.seal(row, authorization)
                return authorization, False
            authorization.refreshing_until = now + 60
            self.seal(row, authorization)
            return authorization, True


class RefreshUnavailable(MCPOAuthError):
    """A transient provider failure, not evidence that consent was revoked."""


class HostedProvider:
    """Runs one personal connection's OAuth operations against its `Authorization`.

    `MCPOAuthProvider` owns the protocol; this class guards every request it sends
    and copies the resulting credentials back for `AuthorizationStore` to seal.
    """

    def __init__(self, record: MCPConnection, authorization: Authorization) -> None:
        self.record = record
        self.authorization = authorization
        self.redirect_uri = f"{dashboard_api_base_url()}{CALLBACK_PATH}"
        self.store = InMemoryOAuthCredentialStore(authorization.credentials)
        self.oauth = MCPOAuthProvider(
            record.url,
            OAuthClientMetadata(
                client_name="Open SWE",
                redirect_uris=[AnyUrl(self.redirect_uri)],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                token_endpoint_auth_method="none",
            ),
            self.store,
            request_sender=self.send,
        )

    async def send(self, request: httpx2.Request) -> httpx2.Response:
        """Send one authorization request only where it may carry what it carries."""
        url = https_mcp_url(str(request.url))
        if request.method == "GET":
            if "authorization" in request.headers or request.content:
                raise MCPOAuthError("OAuth discovery must not carry credentials")
        elif request.method == "POST":
            metadata = self.oauth.context.oauth_metadata
            endpoints = (
                set()
                if metadata is None
                else {
                    httpx2.URL(str(endpoint))
                    for endpoint in (metadata.registration_endpoint, metadata.token_endpoint)
                    if endpoint is not None
                }
            )
            probe = request.url == httpx2.URL(self.record.url) and (
                "authorization" not in request.headers
            )
            if request.url not in endpoints and not probe:
                raise MCPOAuthError(
                    "OAuth credentials may only reach the discovered authorization server endpoints"
                )
        else:
            raise MCPOAuthError("Unsupported OAuth request")
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
            response.status_code, headers=headers, content=bytes(chunks), request=request
        )

    async def begin(self) -> str:
        callback = urlsplit(self.redirect_uri)
        if callback.scheme != "https" and callback.hostname not in {"localhost", "127.0.0.1"}:
            raise MCPOAuthError("Interactive MCP OAuth requires an HTTPS dashboard API URL")
        start = await self.oauth.begin(
            headers=self.record.connection_headers(), state_prefix=f"{self.record.name}."
        )
        credentials = self.store.credentials or OAuthCredentials()
        metadata = credentials.oauth_metadata
        if metadata is None or metadata.authorization_endpoint is None:
            raise MCPOAuthError(
                "This MCP must advertise OAuth discovery and dynamic client registration"
            )
        url = https_mcp_url(start.authorization_url)
        await asyncio.to_thread(resolve_and_validate, url)
        self.authorization.credentials = credentials
        self.authorization.pending = start.pending
        return url

    async def complete(
        self, pending: PendingAuthorization, code: str, state: str, issuer: str | None
    ) -> None:
        await self.oauth.complete(pending, code=code, state=state, iss=issuer)
        self.adopt_tokens()

    async def refresh(self) -> None:
        try:
            await self.oauth.refresh()
        except MCPOAuthUnavailableError, MCPDiscoveryError:
            raise RefreshUnavailable(
                "The MCP authorization server is temporarily unavailable; retry shortly"
            ) from None
        self.adopt_tokens()

    def adopt_tokens(self) -> None:
        credentials = self.store.credentials or OAuthCredentials()
        tokens = credentials.tokens
        if (
            tokens is None
            or not tokens.access_token
            or any(ord(char) < 33 or ord(char) > 126 for char in tokens.access_token)
            or credentials.expires_at is not None
            and credentials.expires_at <= time.time()
        ):
            raise MCPOAuthError("The MCP authorization server returned an invalid token")
        self.authorization.credentials = credentials


async def oauth_status(login: str, record: MCPConnection) -> MCPOAuthStatus:
    authorization = await AuthorizationStore(login, record.name).get()
    if authorization is None or authorization.identity != authorization_identity(record):
        return MCPOAuthStatus(status="not_connected")
    if authorization.status.status == "connecting" and (
        authorization.pending is None or authorization.pending.expires_at <= time.time()
    ):
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
            url = await HostedProvider(record, authorization).begin()
    except MCPOAuthError, MCPDiscoveryError:
        logger.warning("Personal MCP OAuth discovery failed", extra={"mcp_name": record.name})
        raise
    except SharedOAuthError as exc:
        logger.warning("Personal MCP OAuth discovery failed", extra={"mcp_name": record.name})
        raise MCPOAuthError(f"Could not start MCP OAuth: {exc}") from None
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
    authorization, pending = await store.consume(record, state, binding)
    try:
        await HostedProvider(record, authorization).complete(pending, code, state, issuer)
        authorization.status = MCPOAuthStatus(status="connected")
    except Exception:
        logger.warning("Personal MCP OAuth callback failed", extra={"mcp_name": record.name})
        authorization.status = MCPOAuthStatus(
            status="failed", error="Authorization failed; start Connect again"
        )
        raise MCPOAuthError("MCP authorization failed; start Connect again") from None
    finally:
        with anyio.CancelScope(shield=True):
            await store.finish(authorization)


class PersonalOAuthAuth(httpx2.Auth):
    requires_request_body = True

    def __init__(self, login: str, record: MCPConnection) -> None:
        self.login = login
        self.record = record

    async def access_token(self, rejected: str | None = None) -> str:
        store = AuthorizationStore(self.login, self.record.name)
        delay = 0.1
        while True:
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
                        authorization.require_reconnect()
                    finally:
                        authorization.refreshing_until = 0
                        await store.finish(authorization)
            elif (remaining := authorization.refreshing_until - time.time()) > 0:
                await asyncio.sleep(min(delay, remaining))
                delay = min(delay * 2, 1)
                continue
            tokens = authorization.credentials.tokens
            if authorization.status.status != "connected" or tokens is None:
                raise MCPOAuthError(authorization.status.error or _RECONNECT)
            return tokens.access_token

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
