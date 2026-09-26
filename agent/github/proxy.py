"""Track and refresh the GitHub App token baked into a sandbox's proxy.

The LangSmith sandbox proxy is configured once at run start with a GitHub App
installation token. Those tokens expire after exactly one hour, so any agent
run longer than ~1h would start seeing 401s on every ``gh``/``git`` call in the
sandbox. A token also covers only the repositories it was minted for, so one
added to the workspace mid-run would stay unreachable until the next run. This
module records each thread's token and lets a before-model middleware
re-configure the proxy when the token nears expiry or the workspace's
repositories change, and only when the new token differs.
"""

import hashlib
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from agent.config import ENV
from agent.github.app import (
    PermissionKey,
    PermissionMap,
    normalize_permissions,
)
from agent.github.sandbox_access import (
    SandboxGitHubAccess,
    workspace_repositories,
    workspace_token,
)
from agent.sandboxes.state import SANDBOX_BACKENDS, unwrap_sandbox_backend
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG

logger = logging.getLogger(__name__)

# Refresh the proxy token once it is within this window of expiring.
PROXY_TOKEN_REFRESH_WINDOW = timedelta(minutes=5)
# Used only when the token's own expiry is unknown: refresh after this age.
PROXY_TOKEN_FALLBACK_TTL = timedelta(minutes=50)

# thread_id -> (token_expires_at | None, recorded_at, repositories scope | None, permission scope)
_PROXY_TOKEN_EXPIRY: dict[
    str, tuple[datetime | None, datetime, tuple[str, ...] | None, PermissionKey]
] = {}
_PROXY_WORKSPACES: dict[str, str] = {}
_PROXY_BASE_CONFIGS: dict[str, dict[str, Any]] = {}
# thread_id -> (repositories the proxy token was minted for, SHA-256 of that token)
_PROXY_MINTED: dict[str, tuple[frozenset[str], str | None]] = {}
ProxyTokenRecord = tuple[datetime | None, datetime, tuple[str, ...] | None, PermissionKey]


def _parse_expiry(expires_at: Any) -> datetime | None:
    """Best-effort parse of a GitHub ``expires_at`` value to an aware datetime."""
    if expires_at is None:
        return None
    if isinstance(expires_at, datetime):
        return expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
    if isinstance(expires_at, int | float):
        try:
            return datetime.fromtimestamp(float(expires_at), tz=UTC)
        except OverflowError, OSError, ValueError:
            return None
    if isinstance(expires_at, str):
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
    return None


def record_proxy_token_expiry(
    thread_id: str | None,
    expires_at: Any,
    *,
    repositories: Sequence[str] | None = None,
    permissions: PermissionMap | None = None,
    base_proxy_config: dict[str, Any] | None = None,
    workspace_slug: str | None = None,
    minted: SandboxGitHubAccess | None = None,
) -> None:
    """Record when ``thread_id``'s proxy token expires and the repo scope it was minted with.

    ``repositories`` and ``permissions`` preserve the original token scope so a
    later refresh doesn't broaden it to an installation-wide or more privileged token.
    ``minted`` is the access the proxy now holds, which lets a refresh tell when the
    workspace's repositories changed and whether a new token needs configuring.
    """
    if not thread_id:
        return
    if minted is not None:
        _PROXY_MINTED[thread_id] = (minted.repositories, _token_digest(minted.token))
    else:
        _PROXY_MINTED.pop(thread_id, None)
    scope = tuple(repositories) if repositories is not None else None
    _PROXY_WORKSPACES[thread_id] = workspace_slug or DEFAULT_WORKSPACE_SLUG
    _PROXY_TOKEN_EXPIRY[thread_id] = (
        _parse_expiry(expires_at),
        datetime.now(UTC),
        scope,
        normalize_permissions(permissions),
    )
    if base_proxy_config is not None:
        _PROXY_BASE_CONFIGS[thread_id] = dict(base_proxy_config)
    else:
        _PROXY_BASE_CONFIGS.pop(thread_id, None)


def get_recorded_proxy_base_config(thread_id: str | None) -> dict[str, Any] | None:
    if not thread_id:
        return None
    config = _PROXY_BASE_CONFIGS.get(thread_id)
    return dict(config) if config is not None else None


def clear_proxy_token_expiry(thread_id: str | None) -> None:
    if thread_id:
        _PROXY_TOKEN_EXPIRY.pop(thread_id, None)
        _PROXY_WORKSPACES.pop(thread_id, None)
        _PROXY_BASE_CONFIGS.pop(thread_id, None)
        _PROXY_MINTED.pop(thread_id, None)


def _token_digest(token: str | None) -> str | None:
    return hashlib.sha256(token.encode()).hexdigest() if token else None


def _unpack_proxy_token_record(record: tuple[Any, ...]) -> ProxyTokenRecord:
    expires_at, recorded_at, repositories, *rest = record
    permissions = rest[0] if rest else ()
    permission_key = permissions if isinstance(permissions, tuple) else normalize_permissions(None)
    return expires_at, recorded_at, repositories, permission_key


def proxy_token_needs_refresh(thread_id: str | None, *, now: datetime | None = None) -> bool:
    """Whether the recorded proxy token is at/near expiry and should be refreshed."""
    if not thread_id:
        return False
    record = _PROXY_TOKEN_EXPIRY.get(thread_id)
    if record is None:
        return False
    expires_at, recorded_at, _scope, _permissions = _unpack_proxy_token_record(record)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if expires_at is not None:
        return (expires_at - current) <= PROXY_TOKEN_REFRESH_WINDOW
    return (current - recorded_at) >= PROXY_TOKEN_FALLBACK_TTL


async def refresh_proxy_token(
    thread_id: str | None,
    *,
    repositories: Sequence[str] | None = None,
    permissions: PermissionMap | None = None,
) -> bool:
    """Re-configure a LangSmith sandbox proxy with a freshly minted token."""
    if ENV.SANDBOX_TYPE.get() != "langsmith" or not thread_id:
        return False

    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    if sandbox_backend is None:
        return False

    record = _PROXY_TOKEN_EXPIRY.get(thread_id)
    if record is None:
        return False
    _expires, _recorded, recorded_repositories, recorded_permissions = _unpack_proxy_token_record(
        record
    )
    effective_repositories = recorded_repositories
    if repositories is not None:
        requested = {repo.lower() for repo in repositories}
        effective_repositories = tuple(
            sorted(
                requested
                if recorded_repositories is None
                else requested.intersection(repo.lower() for repo in recorded_repositories)
            )
        )
    permission_key = normalize_permissions(permissions) or recorded_permissions
    workspace_slug = _PROXY_WORKSPACES.get(thread_id, DEFAULT_WORKSPACE_SLUG)
    access = await workspace_token(
        workspace_slug,
        repositories=effective_repositories,
        permissions=dict(permission_key) if permission_key else None,
    )

    base_proxy_config = _PROXY_BASE_CONFIGS.get(thread_id)
    minted = _PROXY_MINTED.get(thread_id)
    token_changed = minted is None or minted[1] != _token_digest(access.token)
    if token_changed:
        from agent.sandboxes.providers.langsmith import configure_sandbox_proxy

        current_backend = unwrap_sandbox_backend(sandbox_backend)
        if base_proxy_config is not None:
            await configure_sandbox_proxy(
                current_backend.id,
                access.token,
                base_proxy_config=base_proxy_config,
                thread_id=thread_id,
            )
        else:
            await configure_sandbox_proxy(current_backend.id, access.token, thread_id=thread_id)
    record_proxy_token_expiry(
        thread_id,
        access.expires_at,
        repositories=effective_repositories,
        permissions=dict(permission_key) if permission_key else None,
        base_proxy_config=base_proxy_config,
        workspace_slug=workspace_slug,
        minted=access,
    )
    if token_changed:
        logger.info("Refreshed GitHub proxy token for thread %s", thread_id)
    return token_changed


async def _workspace_repositories_changed(thread_id: str) -> bool:
    minted = _PROXY_MINTED.get(thread_id)
    record = _PROXY_TOKEN_EXPIRY.get(thread_id)
    if minted is None or record is None:
        return False
    _expires, _recorded, scope, _permissions = _unpack_proxy_token_record(record)
    current = await workspace_repositories(
        _PROXY_WORKSPACES.get(thread_id, DEFAULT_WORKSPACE_SLUG), repositories=scope
    )
    return current != minted[0]


async def maybe_refresh_proxy_token(thread_id: str | None, *, now: datetime | None = None) -> bool:
    """Re-configure the sandbox proxy when its token nears expiry or its repositories change.

    Returns True when the proxy was re-configured. Only applies to LangSmith
    sandboxes; other providers don't use the proxy.
    """
    if not thread_id:
        return False
    if not proxy_token_needs_refresh(thread_id, now=now) and not (
        await _workspace_repositories_changed(thread_id)
    ):
        return False
    refreshed = await refresh_proxy_token(thread_id)
    if refreshed:
        logger.info("Refreshed GitHub proxy token for thread %s before expiry", thread_id)
    return refreshed
