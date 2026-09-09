"""Authentication for Linear API calls: OAuth app actor tokens, or a personal API key."""

import asyncio
import logging
from collections.abc import AsyncGenerator

import httpx2
from pydantic import BaseModel

from agent.config import ENV
from agent.utils.http import DEFAULT_HTTP_TIMEOUT

logger = logging.getLogger(__name__)

LINEAR_TOKEN_URL = "https://api.linear.app/oauth/token"

_UNAUTHORIZED = 401


class LinearAuthError(Exception):
    pass


class _TokenResponse(BaseModel):
    access_token: str


class LinearAppTokenProvider:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        scopes: str,
        token_url: str = LINEAR_TOKEN_URL,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._scopes = scopes
        self._token_url = token_url
        self._lock = asyncio.Lock()
        self._token = ""

    async def get_token(self) -> str:
        async with self._lock:
            if not self._token:
                await self._mint()
            return self._token

    async def refresh(self, stale_token: str) -> str:
        async with self._lock:
            if self._token == stale_token:
                await self._mint()
            return self._token

    async def _mint(self) -> None:
        async with httpx2.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
            try:
                response = await client.post(
                    self._token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "scope": self._scopes,
                        "actor": "app",
                    },
                )
                response.raise_for_status()
            except httpx2.HTTPError as exc:
                raise LinearAuthError(f"failed to mint Linear app token: {exc}") from exc
        self._token = _TokenResponse.model_validate(response.json()).access_token
        logger.info("Minted Linear app token", extra={"linear_scopes": self._scopes})


_provider: LinearAppTokenProvider | None = None
_provider_credentials: tuple[str, str, str] | None = None


def app_token_provider() -> LinearAppTokenProvider | None:
    """The shared provider for the configured app credentials, or None in API-key mode."""
    client_id = ENV.LINEAR_OAUTH_CLIENT_ID.get()
    client_secret = ENV.LINEAR_OAUTH_CLIENT_SECRET.get()
    scopes = ENV.LINEAR_OAUTH_SCOPES.get()
    if not client_id or not client_secret:
        return None

    global _provider, _provider_credentials
    credentials = (client_id, client_secret, scopes)
    if _provider is None or _provider_credentials != credentials:
        _provider = LinearAppTokenProvider(
            client_id=client_id, client_secret=client_secret, scopes=scopes
        )
        _provider_credentials = credentials
    return _provider


def linear_app_mode() -> bool:
    return app_token_provider() is not None


def linear_configured() -> bool:
    return linear_app_mode() or bool(ENV.LINEAR_API_KEY.get())


async def linear_authorization_header() -> str | None:
    """``Authorization`` value for Linear calls made outside the GraphQL client."""
    provider = app_token_provider()
    if provider is None:
        return ENV.LINEAR_API_KEY.get() or None
    try:
        return f"Bearer {await provider.get_token()}"
    except LinearAuthError:
        logger.warning("Could not mint a Linear app token", exc_info=True)
        return None


class LinearAuth(httpx2.Auth):
    requires_response_body = False

    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        provider = app_token_provider()
        if provider is None:
            # Linear personal API keys go in Authorization bare, without a Bearer prefix.
            request.headers["Authorization"] = ENV.LINEAR_API_KEY.get()
            yield request
            return

        token = await provider.get_token()
        request.headers["Authorization"] = f"Bearer {token}"
        response = yield request
        if response.status_code == _UNAUTHORIZED:
            refreshed = await provider.refresh(token)
            request.headers["Authorization"] = f"Bearer {refreshed}"
            yield request
