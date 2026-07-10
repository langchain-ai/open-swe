"""Track and refresh the GitHub App token baked into a sandbox's proxy.

The LangSmith sandbox proxy is configured once at run start with a GitHub App
installation token. Those tokens expire after exactly one hour, so any agent
run longer than ~1h would start seeing 401s on every ``gh``/``git`` call in the
sandbox. This module records when each thread's proxy token expires and lets a
before-model middleware re-configure the proxy before it goes stale.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from .github_app import (
    PermissionKey,
    PermissionMap,
    get_github_app_installation_token_with_expiry,
    normalize_permissions,
)
from .sandbox_state import SANDBOX_BACKENDS, unwrap_sandbox_backend

logger = logging.getLogger(__name__)

# Refresh the proxy token once it is within this window of expiring.
PROXY_TOKEN_REFRESH_WINDOW = timedelta(minutes=5)
# Used only when the token's own expiry is unknown: refresh after this age.
PROXY_TOKEN_FALLBACK_TTL = timedelta(minutes=50)

# thread_id -> (token_expires_at | None, recorded_at, repositories scope | None, permission scope)
_PROXY_TOKEN_EXPIRY: dict[
    str, tuple[datetime | None, datetime, tuple[str, ...] | None, PermissionKey]
] = {}
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
        except (OverflowError, OSError, ValueError):
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
) -> None:
    """Record when ``thread_id``'s proxy token expires and the repo scope it was minted with.

    ``repositories`` and ``permissions`` preserve the original token scope so a
    later refresh doesn't broaden it to an installation-wide or more privileged token.
    """
    if not thread_id:
        return
    scope = tuple(repositories) if repositories else None
    _PROXY_TOKEN_EXPIRY[thread_id] = (
        _parse_expiry(expires_at),
        datetime.now(UTC),
        scope,
        normalize_permissions(permissions),
    )


def clear_proxy_token_expiry(thread_id: str | None) -> None:
    if thread_id:
        _PROXY_TOKEN_EXPIRY.pop(thread_id, None)


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
    if os.getenv("SANDBOX_TYPE", "langsmith") != "langsmith" or not thread_id:
        return False

    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    if sandbox_backend is None:
        return False

    _expires, _recorded, recorded_repositories, recorded_permissions = _unpack_proxy_token_record(
        _PROXY_TOKEN_EXPIRY.get(thread_id, (None, None, None, ()))
    )
    effective_repositories = tuple(repositories) if repositories else recorded_repositories
    permission_key = normalize_permissions(permissions) or recorded_permissions
    token_kwargs: dict[str, Any] = {}
    if effective_repositories:
        token_kwargs["repositories"] = list(effective_repositories)
    if permission_key:
        token_kwargs["permissions"] = dict(permission_key)
    token, expires_at = await get_github_app_installation_token_with_expiry(**token_kwargs)
    if not token:
        logger.warning("Proxy token refresh for thread %s failed: no installation token", thread_id)
        return False

    from ..integrations.langsmith import _configure_sandbox_proxy
    from .azure_devops_proxy import ado_proxy_active, resolve_ado_proxy_pat

    ado_pat = await resolve_ado_proxy_pat(thread_id) if ado_proxy_active(thread_id) else None
    current_backend = unwrap_sandbox_backend(sandbox_backend)
    await _configure_sandbox_proxy(current_backend.id, github_token=token, ado_pat=ado_pat)
    record_proxy_token_expiry(
        thread_id,
        expires_at,
        repositories=effective_repositories,
        permissions=dict(permission_key) if permission_key else None,
    )
    logger.info("Refreshed GitHub proxy token for thread %s", thread_id)
    return True


async def maybe_refresh_proxy_token(thread_id: str | None, *, now: datetime | None = None) -> bool:
    """Re-configure the sandbox proxy with a fresh token when near expiry.

    Returns True when a refresh was performed. Only applies to LangSmith
    sandboxes; other providers don't use the proxy.
    """
    if not thread_id or not proxy_token_needs_refresh(thread_id, now=now):
        return False
    refreshed = await refresh_proxy_token(thread_id)
    if refreshed:
        logger.info("Refreshed GitHub proxy token for thread %s before expiry", thread_id)
    return refreshed
