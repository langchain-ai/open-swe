"""Workspace-level OAuth token for Linear, used for all server-side API calls.

Replaces the legacy ``LINEAR_API_KEY`` personal access token with a token
issued by Linear's OAuth ``client_credentials`` grant. Tokens last ~30 days;
this module caches one in process and refetches transparently when it
expires or when an API call returns 401.

The OAuth app itself must be installed into the workspace once with the
``actor=app`` query parameter on the authorize URL so that resources created
with these tokens are attributed to the app (the "open-swe" agent), not to a
user. See ``INSTALLATION.md`` for the one-time setup steps.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

LINEAR_OAUTH_TOKEN_URL = "https://api.linear.app/oauth/token"
_TTL_SAFETY_MARGIN = timedelta(minutes=5)
_DEFAULT_TTL = timedelta(days=30)


class _TokenCache:
    def __init__(self) -> None:
        self._token: str | None = None
        self._expires_at: datetime | None = None
        self._lock = asyncio.Lock()

    async def get(self, *, force_refresh: bool = False) -> str:
        async with self._lock:
            now = datetime.now(UTC)
            if (
                not force_refresh
                and self._token
                and self._expires_at
                and now < self._expires_at - _TTL_SAFETY_MARGIN
            ):
                return self._token
            self._token, self._expires_at = await self._fetch()
            return self._token

    def invalidate(self) -> None:
        self._token = None
        self._expires_at = None

    async def _fetch(self) -> tuple[str, datetime]:
        client_id = os.environ.get("LINEAR_CLIENT_ID", "")
        client_secret = os.environ.get("LINEAR_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            raise RuntimeError(
                "LINEAR_CLIENT_ID / LINEAR_CLIENT_SECRET are not configured; "
                "Linear OAuth app credentials are required."
            )
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                LINEAR_OAUTH_TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "scope": "read,write,app:assignable,app:mentionable",
                },
            )
            response.raise_for_status()
            data = response.json()
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError(f"Linear OAuth response missing access_token: {data}")
        expires_in = data.get("expires_in")
        if isinstance(expires_in, int | float) and expires_in > 0:
            expires_at = datetime.now(UTC) + timedelta(seconds=int(expires_in))
        else:
            expires_at = datetime.now(UTC) + _DEFAULT_TTL
        logger.info("Refreshed Linear app token; expires at %s", expires_at.isoformat())
        return token, expires_at


_cache = _TokenCache()


async def get_linear_app_token(*, force_refresh: bool = False) -> str:
    return await _cache.get(force_refresh=force_refresh)


def invalidate_linear_app_token() -> None:
    _cache.invalidate()
