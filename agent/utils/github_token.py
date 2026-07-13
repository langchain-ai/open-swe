"""GitHub token lookup utilities."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph.config import get_config

logger = logging.getLogger(__name__)

# Treat tokens with <= this many seconds remaining as expired so we re-auth
# before kicking off long agent runs.
_GITHUB_TOKEN_EXPIRY_SKEW_SECONDS = 60
# Hard cap on how long an entry stays cached regardless of the token's own
# expiry, so entries for threads that are never read again don't accumulate.
_GITHUB_TOKEN_MAX_TTL = timedelta(hours=24)
_BOT_PRINCIPAL = "bot"
# (thread_id, principal) -> (token, token_expires_at, cached_at)
_GITHUB_TOKEN_CACHE: dict[tuple[str, str], tuple[str, str | None, datetime]] = {}


def github_token_principal(*, login: str | None = None, email: str | None = None) -> str | None:
    """Return the normalized principal used to isolate cached user tokens."""
    if isinstance(login, str) and login.strip():
        return f"login:{login.strip().casefold()}"
    if isinstance(email, str) and email.strip():
        return f"email:{email.strip().casefold()}"
    return None


class GitHubAuthError(Exception):
    """Raised when a GitHub call returns 401, signalling a stale/revoked token."""


def cache_github_token_for_thread(
    thread_id: str,
    token: str,
    expires_at: str | None = None,
    *,
    principal: str | None = None,
    is_bot_token: bool = False,
) -> None:
    """Cache a GitHub token in process for the current thread and principal."""
    if not thread_id or not token:
        return
    cache_principal = _BOT_PRINCIPAL if is_bot_token else principal
    if not cache_principal:
        logger.warning("Refusing to cache an unbound user GitHub token for thread %s", thread_id)
        return
    now = datetime.now(UTC)
    _GITHUB_TOKEN_CACHE[(thread_id, cache_principal)] = (token, expires_at, now)
    _evict_expired(now=now)


def _is_expired(expires_at: Any, *, now: datetime | None = None) -> bool:
    """Return True when ``expires_at`` is past (or close to) ``now``."""
    if expires_at is None:
        return False

    parsed: datetime | None = None
    if isinstance(expires_at, int | float):
        try:
            parsed = datetime.fromtimestamp(float(expires_at), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return False
    elif isinstance(expires_at, str):
        raw = expires_at.strip()
        if not raw:
            return False
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return False

    if parsed is None:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)

    current = (now or datetime.now(UTC)).astimezone(UTC)
    return (parsed - current).total_seconds() <= _GITHUB_TOKEN_EXPIRY_SKEW_SECONDS


def _entry_expired(expires_at: str | None, cached_at: datetime, *, now: datetime) -> bool:
    """Expired when past the token's own expiry or the 24h cache cap."""
    if now - cached_at >= _GITHUB_TOKEN_MAX_TTL:
        return True
    return _is_expired(expires_at, now=now)


def _evict_expired(*, now: datetime | None = None) -> None:
    current = now or datetime.now(UTC)
    stale = [
        key
        for key, (_token, expires_at, cached_at) in _GITHUB_TOKEN_CACHE.items()
        if _entry_expired(expires_at, cached_at, now=current)
    ]
    for key in stale:
        _GITHUB_TOKEN_CACHE.pop(key, None)


def _cached_token_if_fresh(
    thread_id: str | None, principal: str | None
) -> tuple[str | None, str | None]:
    if not thread_id:
        return None, None
    keys = []
    if principal:
        keys.append((thread_id, principal))
    keys.append((thread_id, _BOT_PRINCIPAL))
    for key in keys:
        cached = _GITHUB_TOKEN_CACHE.get(key)
        if not cached:
            continue
        token, expires_at, cached_at = cached
        if _entry_expired(expires_at, cached_at, now=datetime.now(UTC)):
            _GITHUB_TOKEN_CACHE.pop(key, None)
            logger.info("Cached GitHub token for thread %s has expired; re-resolving", thread_id)
            continue
        return token, expires_at
    return None, None


def _thread_id_from_config(run_config: Mapping[str, Any]) -> str | None:
    configurable = run_config.get("configurable", {})
    if not isinstance(configurable, Mapping):
        return None
    thread_id = configurable.get("thread_id")
    return thread_id if isinstance(thread_id, str) and thread_id else None


def _principal_from_config(run_config: Mapping[str, Any]) -> str | None:
    configurable = run_config.get("configurable", {})
    if not isinstance(configurable, Mapping):
        return None
    return github_token_principal(
        login=configurable.get("github_login"),
        email=configurable.get("user_email"),
    )


def get_github_token(run_config: Mapping[str, Any] | None = None) -> str | None:
    """Resolve the current thread's GitHub token from process memory."""
    resolved = run_config if run_config is not None else get_config()
    token, _expires_at = _cached_token_if_fresh(
        _thread_id_from_config(resolved), _principal_from_config(resolved)
    )
    return token


async def get_github_token_from_thread(
    thread_id: str, *, principal: str | None = None
) -> tuple[str | None, str | None]:
    """Resolve the current process's cached GitHub token for a thread and principal."""
    return _cached_token_if_fresh(thread_id, principal)


async def invalidate_cached_github_token(thread_id: str) -> None:
    """Clear every cached GitHub token for a thread."""
    for key in [key for key in _GITHUB_TOKEN_CACHE if key[0] == thread_id]:
        _GITHUB_TOKEN_CACHE.pop(key, None)
    logger.info("Invalidated cached GitHub token for thread %s", thread_id)
