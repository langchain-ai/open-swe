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
GITHUB_APP_TARGET_REPO_ENV = "GITHUB_APP_TARGET_REPO"

# Installation tokens are valid for 1 hour. Reuse a minted token until it is
# within this window of expiring so chat/review requests don't pay a fresh
# JWT-sign + GitHub round-trip every message. The margin stays above the proxy's
# 5-minute refresh window (``github_proxy.PROXY_TOKEN_REFRESH_WINDOW``) so a
# near-expiry proxy refresh still mints a genuinely fresh token.
_TOKEN_CACHE_MARGIN = timedelta(minutes=10)
_INSTALLATION_CACHE_TTL_SECONDS = 300
PermissionMap = Mapping[str, str]
PermissionKey = tuple[tuple[str, str], ...]
ScopeKey = tuple[str, tuple[int, ...], tuple[str, ...], PermissionKey]

# scope key -> (token, expires_at, good_until). In-process only; never persisted.
_TOKEN_CACHE: dict[ScopeKey, tuple[str, str | None, datetime]] = {}
_INSTALLATION_CACHE: dict[str, tuple[str, float]] = {}


def normalize_permissions(permissions: PermissionMap | None) -> PermissionKey:
    """Return a stable, hashable permission scope key."""
    if not permissions:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in permissions.items() if str(k) and str(v)))


def _scope_key(
    installation_id: str,
    repository_ids: Sequence[int] | None,
    repositories: Sequence[str] | None,
    permissions: PermissionMap | None = None,
) -> ScopeKey:
    """Cache key segregating installation, repo, and permission-scoped tokens."""
    ids = tuple(sorted(int(i) for i in repository_ids)) if repository_ids else ()
    names = tuple(sorted(str(r) for r in repositories)) if repositories else ()
    return installation_id, ids, names, normalize_permissions(permissions)


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
    """Drop all cached installation tokens and repository mappings."""
    _TOKEN_CACHE.clear()
    _INSTALLATION_CACHE.clear()


def _monotonic() -> float:
    return time.monotonic()


def _normalize_target_repo(target_repo: str | None) -> str | None:
    """Return canonical owner/repo context from an argument or the environment."""
    raw = target_repo if target_repo is not None else os.environ.get(GITHUB_APP_TARGET_REPO_ENV)
    if raw is None:
        return None
    if not raw.strip():
        raise ValueError("GitHub App target repository cannot be blank")
    parts = [part.strip() for part in raw.strip().split("/")]
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Invalid GitHub App target repository: {raw!r}")
    return f"{parts[0]}/{parts[1]}"


async def _resolve_installation_id(target_repo: str | None) -> str:
    """Resolve the installation covering a target repo or use the pinned fallback."""
    normalized = _normalize_target_repo(target_repo)
    if normalized is None:
        if not GITHUB_APP_INSTALLATION_ID:
            raise ValueError("GITHUB_APP_INSTALLATION_ID is required without repository context")
        return GITHUB_APP_INSTALLATION_ID

    key = normalized.lower()
    cached = _INSTALLATION_CACHE.get(key)
    now = _monotonic()
    if cached is not None and cached[1] > now:
        return cached[0]

    app_jwt = _generate_app_jwt()
    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
        response = await client.get(
            f"https://api.github.com/repos/{normalized}/installation",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        response.raise_for_status()
        installation_id = response.json().get("id")
        if not isinstance(installation_id, int | str) or not str(installation_id):
            raise ValueError(f"GitHub returned no installation for {normalized}")
        resolved = str(installation_id)
        _INSTALLATION_CACHE[key] = (resolved, now + _INSTALLATION_CACHE_TTL_SECONDS)
        return resolved


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
    target_repo: str | None = None,
    repository_ids: Sequence[int] | None = None,
    repositories: Sequence[str] | None = None,
    permissions: PermissionMap | None = None,
    log_errors: bool = True,
) -> str | None:
    """Exchange the GitHub App JWT for an installation access token."""
    token, _ = await get_github_app_installation_token_with_expiry(
        target_repo=target_repo,
        repository_ids=repository_ids,
        repositories=repositories,
        permissions=permissions,
        log_errors=log_errors,
    )
    return token


async def get_github_app_installation_token_with_expiry(
    *,
    target_repo: str | None = None,
    repository_ids: Sequence[int] | None = None,
    repositories: Sequence[str] | None = None,
    permissions: PermissionMap | None = None,
    log_errors: bool = True,
) -> tuple[str | None, str | None]:
    """Exchange the GitHub App JWT for an installation access token and its expiry."""
    if not GITHUB_APP_ID or not GITHUB_APP_PRIVATE_KEY:
        logger.debug("GitHub App credentials not fully configured, skipping app token")
        return None, None

    body: dict[str, Any] = {}
    if repository_ids:
        body["repository_ids"] = list(repository_ids)
    elif repositories:
        body["repositories"] = list(repositories)
    permission_key = normalize_permissions(permissions)
    if permission_key:
        body["permissions"] = dict(permission_key)

    try:
        installation_id = await _resolve_installation_id(target_repo)
        key = _scope_key(installation_id, repository_ids, repositories, permissions)
        now = datetime.now(UTC)
        cached = _cached_token(key, now=now)
        if cached is not None:
            return cached

        app_jwt = _generate_app_jwt()
        async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
            response = await client.post(
                f"https://api.github.com/app/installations/{installation_id}/access_tokens",
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
