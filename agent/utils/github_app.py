"""GitHub App installation token generation."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt

from .http import DEFAULT_HTTP_TIMEOUT

logger = logging.getLogger(__name__)

GITHUB_APP_ID = os.environ.get("GITHUB_APP_ID", "")
GITHUB_APP_PRIVATE_KEY = os.environ.get("GITHUB_APP_PRIVATE_KEY", "")
GITHUB_APP_INSTALLATION_ID = os.environ.get("GITHUB_APP_INSTALLATION_ID", "")

# Installation tokens are valid for 1 hour. Reuse a minted token until it is
# within this window of expiring so chat/review requests don't pay a fresh
# JWT-sign + GitHub round-trip every message. The margin stays above the proxy's
# 5-minute refresh window (``github_proxy.PROXY_TOKEN_REFRESH_WINDOW``) so a
# near-expiry proxy refresh still mints a genuinely fresh token.
_TOKEN_CACHE_MARGIN = timedelta(minutes=10)
BASE_RUNTIME_PROXY_TOKEN_PERMISSIONS: dict[str, str] = {
    "contents": "write",
    "pull_requests": "write",
    "issues": "write",
    "checks": "write",
}
RUNTIME_PROXY_TOKEN_PERMISSIONS: dict[str, str] = {
    **BASE_RUNTIME_PROXY_TOKEN_PERMISSIONS,
    "actions": "read",
}
WORKFLOW_RUNTIME_PROXY_TOKEN_PERMISSIONS: dict[str, str] = {
    **BASE_RUNTIME_PROXY_TOKEN_PERMISSIONS,
    "workflows": "write",
}

PermissionMap = Mapping[str, str]
PermissionKey = tuple[tuple[str, str], ...]
ScopeKey = tuple[tuple[int, ...], tuple[str, ...], PermissionKey]

# scope key -> (token, expires_at, good_until). In-process only; never persisted.
_TOKEN_CACHE: dict[ScopeKey, tuple[str, str | None, datetime]] = {}


def normalize_permissions(permissions: PermissionMap | None) -> PermissionKey:
    """Return a stable, hashable permission scope key."""
    if not permissions:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in permissions.items() if str(k) and str(v)))


def _scope_key(
    repository_ids: Sequence[int] | None,
    repositories: Sequence[str] | None,
    permissions: PermissionMap | None = None,
) -> ScopeKey:
    """Cache key segregating repo and permission-scoped tokens."""
    ids = tuple(sorted(int(i) for i in repository_ids)) if repository_ids else ()
    names = tuple(sorted(str(r) for r in repositories)) if repositories else ()
    return ids, names, normalize_permissions(permissions)


def _parse_expiry(expires_at: Any) -> datetime | None:
    """Best-effort parse of a GitHub ``expires_at`` ISO timestamp to a UTC datetime."""
    if not isinstance(expires_at, str):
        return None
    raw = expires_at.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _cached_token(key: ScopeKey, *, now: datetime) -> tuple[str, str | None] | None:
    cached = _TOKEN_CACHE.get(key)
    if cached is None:
        return None
    token, expires_at, good_until = cached
    if now < good_until:
        return token, expires_at
    _TOKEN_CACHE.pop(key, None)
    return None


def clear_app_token_cache() -> None:
    """Drop all cached installation tokens (test/maintenance hook)."""
    _TOKEN_CACHE.clear()


def _generate_app_jwt() -> str:
    """Generate a short-lived JWT signed with the GitHub App private key."""
    now = int(time.time())
    payload = {
        "iat": now - 60,  # issued 60s ago to account for clock skew
        "exp": now + 540,  # expires in 9 minutes (max is 10)
        "iss": GITHUB_APP_ID,
    }
    private_key = GITHUB_APP_PRIVATE_KEY.replace("\\n", "\n")
    return jwt.encode(payload, private_key, algorithm="RS256")


async def get_github_app_installation_token(
    *,
    repository_ids: Sequence[int] | None = None,
    repositories: Sequence[str] | None = None,
    permissions: PermissionMap | None = None,
    log_errors: bool = True,
) -> str | None:
    """Exchange the GitHub App JWT for an installation access token."""
    token, _ = await get_github_app_installation_token_with_expiry(
        repository_ids=repository_ids,
        repositories=repositories,
        permissions=permissions,
        log_errors=log_errors,
    )
    return token


async def get_github_app_installation_token_with_expiry(
    *,
    repository_ids: Sequence[int] | None = None,
    repositories: Sequence[str] | None = None,
    permissions: PermissionMap | None = None,
    log_errors: bool = True,
) -> tuple[str | None, str | None]:
    """Exchange the GitHub App JWT for an installation access token and its expiry."""
    if not GITHUB_APP_ID or not GITHUB_APP_PRIVATE_KEY or not GITHUB_APP_INSTALLATION_ID:
        logger.debug("GitHub App env vars not fully configured, skipping app token")
        return None, None

    key = _scope_key(repository_ids, repositories, permissions)
    now = datetime.now(UTC)
    cached = _cached_token(key, now=now)
    if cached is not None:
        return cached

    body: dict[str, Any] = {}
    if repository_ids:
        body["repository_ids"] = list(repository_ids)
    elif repositories:
        body["repositories"] = list(repositories)
    permission_key = normalize_permissions(permissions)
    if permission_key:
        body["permissions"] = dict(permission_key)

    try:
        app_jwt = _generate_app_jwt()
        async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
            response = await client.post(
                f"https://api.github.com/app/installations/{GITHUB_APP_INSTALLATION_ID}/access_tokens",
                headers={
                    "Authorization": f"Bearer {app_jwt}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json=body or None,
            )
            response.raise_for_status()
            data = response.json()
            token, expires_at = data.get("token"), data.get("expires_at")
            parsed = _parse_expiry(expires_at)
            if isinstance(token, str) and token and parsed is not None:
                _TOKEN_CACHE[key] = (token, expires_at, parsed - _TOKEN_CACHE_MARGIN)
            return token, expires_at
    except Exception:
        if log_errors:
            logger.exception("Failed to get GitHub App installation token")
        else:
            logger.debug("Failed to get GitHub App installation token", exc_info=True)
        return None, None
