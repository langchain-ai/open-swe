"""GitHub App installation token generation."""

import logging
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import jwt

from ..config import github_app_id, github_app_installation_id, github_app_private_key
from ..utils.timestamps import parse_expiry
from .api import github_client, github_request, github_url

logger = logging.getLogger(__name__)

# Installation tokens are valid for 1 hour. Reuse a minted token until it is
# within this window of expiring so chat/review requests don't pay a fresh
# JWT-sign + GitHub round-trip every message. The margin stays above the proxy's
# 5-minute refresh window (``github_proxy.PROXY_TOKEN_REFRESH_WINDOW``) so a
# near-expiry proxy refresh still mints a genuinely fresh token.
_TOKEN_CACHE_MARGIN = timedelta(minutes=10)
PermissionMap = Mapping[str, str]
PermissionKey = tuple[tuple[str, str], ...]
ScopeKey = tuple[str, tuple[int, ...], tuple[str, ...], PermissionKey]

# scope key -> (token, expires_at, good_until). In-process only; never persisted.
_TOKEN_CACHE: dict[ScopeKey, tuple[str, str | None, datetime]] = {}


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
        "iss": github_app_id(),
    }
    private_key = github_app_private_key().replace("\\n", "\n")
    return jwt.encode(payload, private_key, algorithm="RS256")


async def get_github_app_installation_id_for_org(org: str) -> int | None:
    """Resolve the GitHub App installation for an organization."""
    if not github_app_id() or not github_app_private_key() or not org.strip():
        return None
    url = github_url(f"/orgs/{quote(org.strip(), safe='')}/installation")
    try:
        async with github_client(token=_generate_app_jwt()) as client:
            response = await github_request(client, "GET", url)
        response.raise_for_status()
        installation_id = response.json().get("id")
        return installation_id if isinstance(installation_id, int) and installation_id > 0 else None
    except Exception:
        logger.warning("Failed to resolve GitHub App installation for %s", org, exc_info=True)
        return None


async def get_github_app_installation_id_for_repo(owner: str, repo: str) -> int | None:
    """Resolve the GitHub App installation that can access a repository."""
    if not github_app_id() or not github_app_private_key() or not owner.strip() or not repo.strip():
        return None
    url = github_url(
        f"/repos/{quote(owner.strip(), safe='')}/{quote(repo.strip(), safe='')}/installation"
    )
    try:
        async with github_client(token=_generate_app_jwt()) as client:
            response = await github_request(client, "GET", url)
        response.raise_for_status()
        installation_id = response.json().get("id")
        return installation_id if isinstance(installation_id, int) and installation_id > 0 else None
    except Exception:
        logger.warning(
            "Failed to resolve GitHub App installation for %s/%s", owner, repo, exc_info=True
        )
        return None


async def get_github_app_installation_token(
    *,
    installation_id: str | int | None = None,
    repository_ids: Sequence[int] | None = None,
    repositories: Sequence[str] | None = None,
    permissions: PermissionMap | None = None,
    log_errors: bool = True,
) -> str | None:
    """Exchange the GitHub App JWT for an installation access token."""
    token, _ = await get_github_app_installation_token_with_expiry(
        installation_id=installation_id,
        repository_ids=repository_ids,
        repositories=repositories,
        permissions=permissions,
        log_errors=log_errors,
    )
    return token


async def get_github_app_installation_token_with_expiry(
    *,
    installation_id: str | int | None = None,
    repository_ids: Sequence[int] | None = None,
    repositories: Sequence[str] | None = None,
    permissions: PermissionMap | None = None,
    log_errors: bool = True,
) -> tuple[str | None, str | None]:
    """Exchange the GitHub App JWT for an installation access token and its expiry."""
    resolved_installation_id = str(
        github_app_installation_id() if installation_id is None else installation_id
    ).strip()
    if (
        not github_app_id()
        or not github_app_private_key()
        or not resolved_installation_id.isdigit()
        or int(resolved_installation_id) <= 0
    ):
        logger.debug("GitHub App env vars not fully configured, skipping app token")
        return None, None

    key = _scope_key(resolved_installation_id, repository_ids, repositories, permissions)
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
        async with github_client(token=app_jwt) as client:
            response = await github_request(
                client,
                "POST",
                github_url(f"/app/installations/{resolved_installation_id}/access_tokens"),
                json=body or None,
            )
            response.raise_for_status()
            data = response.json()
            token, expires_at = data.get("token"), data.get("expires_at")
            parsed = parse_expiry(expires_at)
            if isinstance(token, str) and token and parsed is not None:
                _TOKEN_CACHE[key] = (token, expires_at, parsed - _TOKEN_CACHE_MARGIN)
            return token, expires_at
    except Exception:
        if log_errors:
            logger.exception("Failed to get GitHub App installation token")
        else:
            logger.debug("Failed to get GitHub App installation token", exc_info=True)
        return None, None
