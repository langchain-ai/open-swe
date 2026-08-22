"""Configure, track and refresh the GitHub App token baked into a sandbox's proxy.

Every path that points a sandbox proxy at a GitHub token goes through
``configure_proxy_for_sandbox``: run start, sandbox reuse, and the mid-run
refresh alike. Installation tokens expire after exactly one hour, so any run
longer than ~1h would start seeing 401s on every ``gh``/``git`` call in the
sandbox; configuring in one place is what guarantees every one of them records
an expiry, and that a refresh re-mints with the same scope rather than
broadening it.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from deepagents.backends.protocol import SandboxBackendProtocol

from ..sandboxes.providers import sandbox_provider_uses_proxy
from ..sandboxes.proxy import unwrap_sandbox_backend
from ..sandboxes.registry import SANDBOX_BACKENDS
from ..utils.timestamps import parse_expiry
from .app import (
    PermissionKey,
    PermissionMap,
    get_github_app_installation_token_with_expiry,
    normalize_permissions,
)

logger = logging.getLogger(__name__)

# Refresh the proxy token once it is within this window of expiring.
PROXY_TOKEN_REFRESH_WINDOW = timedelta(minutes=5)
# Used only when the token's own expiry is unknown: refresh after this age.
PROXY_TOKEN_FALLBACK_TTL = timedelta(minutes=50)

# thread_id -> (token_expires_at | None, recorded_at, repositories scope | None, permission scope)
_PROXY_TOKEN_EXPIRY: dict[
    str, tuple[datetime | None, datetime, tuple[str, ...] | None, PermissionKey]
] = {}
_PROXY_BASE_CONFIGS: dict[str, dict[str, Any]] = {}
ProxyTokenRecord = tuple[datetime | None, datetime, tuple[str, ...] | None, PermissionKey]


def record_proxy_token_expiry(
    thread_id: str | None,
    expires_at: Any,
    *,
    repositories: Sequence[str] | None = None,
    permissions: PermissionMap | None = None,
    base_proxy_config: Mapping[str, Any] | None = None,
) -> None:
    """Record when ``thread_id``'s proxy token expires and the repo scope it was minted with.

    ``repositories`` and ``permissions`` preserve the original token scope so a
    later refresh doesn't broaden it to an installation-wide or more privileged
    token; ``base_proxy_config`` preserves the sandbox's own proxy rules so a
    refresh doesn't drop them.
    """
    if not thread_id:
        return
    scope = tuple(repositories) if repositories else None
    _PROXY_TOKEN_EXPIRY[thread_id] = (
        parse_expiry(expires_at),
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
        _PROXY_BASE_CONFIGS.pop(thread_id, None)


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


async def configure_proxy_for_sandbox(
    sandbox_backend: SandboxBackendProtocol,
    *,
    thread_id: str | None = None,
    github_token: str | None = None,
    repositories: Sequence[str] | None = None,
    permissions: PermissionMap | None = None,
    base_proxy_config: Mapping[str, Any] | None = None,
) -> bool:
    """Point ``sandbox_backend``'s proxy at a GitHub token and record its expiry.

    ``github_token`` overrides minting for callers that already hold a token
    (a dashboard user's OAuth token, say); otherwise an installation token is
    minted for exactly ``repositories``/``permissions``. ``base_proxy_config``
    is the sandbox's own proxy settings, which the GitHub rules are layered on
    rather than replacing; it is recorded with the expiry so a refresh re-sends
    it. Returns False — without touching the sandbox — when the provider has no
    proxy or no token could be minted; the caller decides whether that is fatal.
    """
    if not sandbox_provider_uses_proxy():
        return False

    scope = tuple(repositories) if repositories else None
    permission_key = normalize_permissions(permissions)
    if github_token:
        token, expires_at = github_token, None
    else:
        token_kwargs: dict[str, Any] = {}
        if scope:
            token_kwargs["repositories"] = list(scope)
        if permission_key:
            token_kwargs["permissions"] = dict(permission_key)
        token, expires_at = await get_github_app_installation_token_with_expiry(**token_kwargs)
    if not token:
        logger.warning(
            "Cannot configure GitHub proxy for thread %s: no installation token", thread_id
        )
        return False

    # Imported here so the LangSmith SDK stays out of the import graph of every
    # module that only wants to record an expiry.
    from ..integrations.langsmith import configure_github_proxy

    await configure_github_proxy(
        unwrap_sandbox_backend(sandbox_backend).id, token, base_proxy_config=base_proxy_config
    )
    record_proxy_token_expiry(
        thread_id,
        expires_at,
        repositories=scope,
        permissions=dict(permission_key) if permission_key else None,
        base_proxy_config=base_proxy_config,
    )
    return True


async def refresh_proxy_for_thread(
    thread_id: str | None,
    *,
    repositories: Sequence[str] | None = None,
    permissions: PermissionMap | None = None,
) -> bool:
    """Re-configure the proxy of the sandbox currently bound to ``thread_id``.

    Falls back to the scope the thread's token was last minted with, so a
    refresh never hands the sandbox a broader token than it started with.
    """
    if not thread_id:
        return False
    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    if sandbox_backend is None:
        return False

    _expires, _recorded, recorded_repositories, recorded_permissions = _unpack_proxy_token_record(
        _PROXY_TOKEN_EXPIRY.get(thread_id, (None, None, None, ()))
    )
    permission_key = normalize_permissions(permissions) or recorded_permissions
    refreshed = await configure_proxy_for_sandbox(
        sandbox_backend,
        thread_id=thread_id,
        repositories=repositories or recorded_repositories,
        permissions=dict(permission_key) if permission_key else None,
        base_proxy_config=_PROXY_BASE_CONFIGS.get(thread_id),
    )
    if refreshed:
        logger.info("Refreshed GitHub proxy token for thread %s", thread_id)
    return refreshed


async def maybe_refresh_proxy_token(thread_id: str | None, *, now: datetime | None = None) -> bool:
    """Re-configure the sandbox proxy with a fresh token when near expiry."""
    if not thread_id or not proxy_token_needs_refresh(thread_id, now=now):
        return False
    return await refresh_proxy_for_thread(thread_id)
