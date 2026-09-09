"""OAuth client credentials for scoped MCP connections."""

import asyncio
from collections.abc import AsyncGenerator
from functools import lru_cache
from time import monotonic
from urllib.parse import quote_plus

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from agent.encryption import decrypt_token
from agent.mcp.models import MCPConnection, MCPOAuth
from agent.mcp.transport import mcp_http_client


class MCPOAuthError(ValueError):
    """A safe OAuth failure that may be shown to an administrator."""


class _Token(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True, allow_inf_nan=False)

    access_token: SecretStr = Field(min_length=1, max_length=32768)
    token_type: str
    expires_in: float = Field(default=300, gt=0)


class _ClientCredentialsAuth(httpx.Auth):
    requires_request_body = True

    def __init__(self, settings: MCPOAuth, encrypted_secret: str) -> None:
        self._settings = settings
        self._encrypted_secret = encrypted_secret
        self._token = ""
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def _access_token(self, rejected: str | None = None) -> str:
        async with self._lock:
            if self._token and self._token != rejected and monotonic() < self._expires_at:
                return self._token
            settings = self._settings
            try:
                secret = decrypt_token(self._encrypted_secret)
                if not secret:
                    raise ValueError("Missing client secret")
                body = {"grant_type": "client_credentials"}
                if settings.scope:
                    body["scope"] = settings.scope
                auth = None
                if settings.token_endpoint_auth_method == "client_secret_basic":
                    auth = httpx.BasicAuth(quote_plus(settings.client_id), quote_plus(secret))
                else:
                    body.update(client_id=settings.client_id, client_secret=secret)
                async with mcp_http_client(settings.token_url, auth=auth) as client:
                    response = await client.post(
                        settings.token_url, data=body, headers={"Accept": "application/json"}
                    )
                if not response.is_success:
                    raise MCPOAuthError(
                        f"OAuth token request failed (HTTP {response.status_code}); check the token URL, client credentials, and scopes"
                    )
                token = _Token.model_validate(response.json())
                access_token = token.access_token.get_secret_value()
                if token.token_type.lower() != "bearer" or any(
                    ord(char) < 33 or ord(char) > 126 for char in access_token
                ):
                    raise ValueError("Invalid bearer token")
            except MCPOAuthError:
                raise
            except Exception:
                raise MCPOAuthError(
                    "OAuth token request failed; check the token URL and client credentials"
                ) from None
            self._token = access_token
            self._expires_at = monotonic() + token.expires_in - min(60, token.expires_in / 10)
            return access_token

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        token = await self._access_token()
        request.headers["Authorization"] = f"Bearer {token}"
        response = yield request
        if response.status_code == 401:
            await response.aread()
            request.headers["Authorization"] = f"Bearer {await self._access_token(rejected=token)}"
            yield request


@lru_cache(maxsize=128)
def _cached_auth(
    _identity: tuple[str, ...], settings: MCPOAuth, encrypted_secret: str
) -> httpx.Auth:
    # Credentials and settings identify the cache entry, so rotation cannot reuse old tokens.
    return _ClientCredentialsAuth(settings, encrypted_secret)


def connection_auth(record: MCPConnection, namespace: tuple[str, ...]) -> httpx.Auth | None:
    if record.oauth is None:
        return None
    return _cached_auth((*namespace, record.name), record.oauth, record.encrypted_client_secret)
