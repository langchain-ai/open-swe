"""GitHub App installation token generation."""

import logging
import os
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx
import jwt

from agent.utils.http import DEFAULT_HTTP_TIMEOUT

logger = logging.getLogger(__name__)

GITHUB_APP_ID = os.environ.get("GITHUB_APP_ID", "")
GITHUB_APP_CLIENT_ID = os.environ.get("GITHUB_APP_CLIENT_ID", "")
GITHUB_APP_PRIVATE_KEY = os.environ.get("GITHUB_APP_PRIVATE_KEY", "")
GITHUB_APP_INSTALLATION_ID = os.environ.get("GITHUB_APP_INSTALLATION_ID", "")

GITHUB_API_BASE_URL = "https://api.github.com"

# Installation tokens are valid for 1 hour. Reuse a minted token until it is
# within this window of expiring so chat/review requests don't pay a fresh
# JWT-sign + GitHub round-trip every message. The margin stays above the proxy's
# 5-minute refresh window (``github_proxy.PROXY_TOKEN_REFRESH_WINDOW``) so a
# near-expiry proxy refresh still mints a genuinely fresh token.
_TOKEN_CACHE_MARGIN = timedelta(minutes=10)
_INSTALLATION_CACHE_TTL = timedelta(hours=1)
_DISCOVERY_RETRY_INTERVAL = timedelta(minutes=5)
_INSTALLATIONS_PAGE_SIZE = 100
_INSTALLATIONS_MAX_PAGES = 10
PermissionMap = Mapping[str, str]
PermissionKey = tuple[tuple[str, str], ...]
ScopeKey = tuple[str, tuple[int, ...], tuple[str, ...], PermissionKey]

# scope key -> (token, expires_at, good_until). In-process only; never persisted.
_TOKEN_CACHE: dict[ScopeKey, tuple[str, str | None, datetime]] = {}
# (owner, repo or "") -> (installation id, cached_at)
_INSTALLATION_ID_CACHE: dict[tuple[str, str], tuple[str, datetime]] = {}
# Outcome of the app-wide single-installation lookup: (id or None, checked_at)
_SINGLE_INSTALLATION: tuple[str | None, datetime] | None = None


def _app_jwt_issuer() -> str:
    """GitHub accepts either the client ID or the numeric app ID as ``iss``.

    The client ID is the documented recommendation and is already required for
    dashboard login, so ``GITHUB_APP_ID`` is only consulted when it is unset.
    """
    return GITHUB_APP_CLIENT_ID or GITHUB_APP_ID


def _app_credentials_configured() -> bool:
    return bool(_app_jwt_issuer() and GITHUB_APP_PRIVATE_KEY)


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
    """Drop cached installation tokens and discovery results (test/maintenance hook)."""
    global _SINGLE_INSTALLATION
    _TOKEN_CACHE.clear()
    _INSTALLATION_ID_CACHE.clear()
    _SINGLE_INSTALLATION = None


def _generate_app_jwt() -> str:
    """Generate a short-lived JWT signed with the GitHub App private key."""
    now = int(time.time())
    payload = {
        "iat": now - 60,  # issued 60s ago to account for clock skew
        "exp": now + 540,  # expires in 9 minutes (max is 10)
        "iss": _app_jwt_issuer(),
    }
    private_key = GITHUB_APP_PRIVATE_KEY.replace("\\n", "\n")
    return jwt.encode(payload, private_key, algorithm="RS256")


def _app_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_generate_app_jwt()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def get_github_app_installation_id_for_org(org: str) -> int | None:
    """Resolve the GitHub App installation for an organization."""
    if not _app_credentials_configured() or not org.strip():
        return None
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
            response = await client.get(
                f"{GITHUB_API_BASE_URL}/orgs/{quote(org.strip(), safe='')}/installation",
                headers=_app_headers(),
            )
        response.raise_for_status()
        installation_id = response.json().get("id")
        return installation_id if isinstance(installation_id, int) and installation_id > 0 else None
    except Exception:
        logger.warning("Failed to resolve GitHub App installation for %s", org, exc_info=True)
        return None


async def get_github_app_installation_id_for_repo(owner: str, repo: str) -> int | None:
    """Resolve the GitHub App installation that can access a repository."""
    if not _app_credentials_configured() or not owner.strip() or not repo.strip():
        return None
    url = (
        f"{GITHUB_API_BASE_URL}/repos/"
        f"{quote(owner.strip(), safe='')}/{quote(repo.strip(), safe='')}/installation"
    )
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
            response = await client.get(url, headers=_app_headers())
        response.raise_for_status()
        installation_id = response.json().get("id")
        return installation_id if isinstance(installation_id, int) and installation_id > 0 else None
    except Exception:
        logger.warning(
            "Failed to resolve GitHub App installation for %s/%s", owner, repo, exc_info=True
        )
        return None


async def list_app_installations() -> list[dict[str, Any]]:
    """Every installation of this GitHub App, across all accounts."""
    if not _app_credentials_configured():
        return []
    installations: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
        for page in range(1, _INSTALLATIONS_MAX_PAGES + 1):
            response = await client.get(
                f"{GITHUB_API_BASE_URL}/app/installations"
                f"?per_page={_INSTALLATIONS_PAGE_SIZE}&page={page}",
                headers=_app_headers(),
            )
            response.raise_for_status()
            batch = response.json()
            if not isinstance(batch, list):
                break
            installations.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < _INSTALLATIONS_PAGE_SIZE:
                break
    return installations


def _installation_account(installation: Mapping[str, Any]) -> str:
    account = installation.get("account")
    login = account.get("login") if isinstance(account, Mapping) else None
    return str(login) if login else "?"


async def _discover_single_installation() -> str | None:
    """The app's only installation, or ``None`` when there are zero or several.

    Failures and ambiguous results are remembered for a few minutes so a busy
    webhook path does not re-list installations on every token mint.
    """
    global _SINGLE_INSTALLATION
    now = datetime.now(UTC)
    if _SINGLE_INSTALLATION is not None:
        cached_id, checked_at = _SINGLE_INSTALLATION
        if cached_id is not None or now - checked_at < _DISCOVERY_RETRY_INTERVAL:
            return cached_id
    try:
        installations = await list_app_installations()
    except Exception:
        logger.warning("Failed to list GitHub App installations", exc_info=True)
        _SINGLE_INSTALLATION = (None, now)
        return None
    ids = [
        str(item["id"])
        for item in installations
        if isinstance(item.get("id"), int) and item["id"] > 0
    ]
    if len(ids) == 1:
        logger.info(
            "Using the GitHub App's only installation (id %s, account %s)",
            ids[0],
            _installation_account(installations[0]),
        )
        _SINGLE_INSTALLATION = (ids[0], now)
        return ids[0]
    if not ids:
        logger.warning("The GitHub App is not installed on any account yet")
    else:
        logger.warning(
            "The GitHub App has %d installations (%s); set GITHUB_APP_INSTALLATION_ID or "
            "rely on repository context to choose one",
            len(ids),
            ", ".join(_installation_account(item) for item in installations),
        )
    _SINGLE_INSTALLATION = (None, now)
    return None


async def _cached_context_installation(owner: str, repo: str | None) -> str | None:
    key = (owner.strip().lower(), (repo or "").strip().lower())
    now = datetime.now(UTC)
    cached = _INSTALLATION_ID_CACHE.get(key)
    if cached is not None and now - cached[1] < _INSTALLATION_CACHE_TTL:
        return cached[0]
    resolved = (
        await get_github_app_installation_id_for_repo(owner, repo)
        if repo
        else await get_github_app_installation_id_for_org(owner)
    )
    if resolved is None:
        return None
    _INSTALLATION_ID_CACHE[key] = (str(resolved), now)
    return str(resolved)


async def resolve_default_installation_id(
    *, owner: str | None = None, repo: str | None = None
) -> str | None:
    """Installation to mint tokens under when the caller did not name one.

    Precedence: ``GITHUB_APP_INSTALLATION_ID`` → the installation that owns
    ``owner/repo`` (or ``owner``) → the app's only installation → ``None``.
    """
    env_id = GITHUB_APP_INSTALLATION_ID.strip()
    if env_id:
        return env_id
    if not _app_credentials_configured():
        return None
    if owner and owner.strip():
        contextual = await _cached_context_installation(owner, repo)
        if contextual:
            return contextual
    return await _discover_single_installation()


async def get_github_app_installation_token(
    *,
    installation_id: str | int | None = None,
    repository_ids: Sequence[int] | None = None,
    repositories: Sequence[str] | None = None,
    permissions: PermissionMap | None = None,
    owner: str | None = None,
    repo: str | None = None,
    log_errors: bool = True,
) -> str | None:
    """Exchange the GitHub App JWT for an installation access token."""
    token, _ = await get_github_app_installation_token_with_expiry(
        installation_id=installation_id,
        repository_ids=repository_ids,
        repositories=repositories,
        permissions=permissions,
        owner=owner,
        repo=repo,
        log_errors=log_errors,
    )
    return token


async def get_github_app_installation_token_with_expiry(
    *,
    installation_id: str | int | None = None,
    repository_ids: Sequence[int] | None = None,
    repositories: Sequence[str] | None = None,
    permissions: PermissionMap | None = None,
    owner: str | None = None,
    repo: str | None = None,
    log_errors: bool = True,
) -> tuple[str | None, str | None]:
    """Exchange the GitHub App JWT for an installation access token and its expiry.

    ``owner``/``repo`` only steer installation discovery when ``installation_id``
    is omitted; they do not scope the token (use ``repositories`` for that).
    """
    if installation_id is None:
        resolved = await resolve_default_installation_id(owner=owner, repo=repo)
    else:
        resolved = str(installation_id)
    resolved_installation_id = (resolved or "").strip()
    if (
        not _app_credentials_configured()
        or not resolved_installation_id.isdigit()
        or int(resolved_installation_id) <= 0
    ):
        logger.debug("GitHub App not fully configured, skipping app token")
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
        async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
            response = await client.post(
                f"{GITHUB_API_BASE_URL}/app/installations/{resolved_installation_id}/access_tokens",
                headers=_app_headers(),
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
