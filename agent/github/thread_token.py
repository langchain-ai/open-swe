"""GitHub token lookup utilities."""

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple

from langgraph.config import get_config

from agent.github.app import get_github_app_installation_token_with_expiry
from agent.run_config import RunConfig

logger = logging.getLogger(__name__)

# Treat tokens with <= this many seconds remaining as expired so we re-auth
# before kicking off long agent runs.
_GITHUB_TOKEN_EXPIRY_SKEW_SECONDS = 60
# Hard cap on how long an entry stays cached regardless of the token's own
# expiry, so entries for threads that are never read again don't accumulate.
_GITHUB_TOKEN_MAX_TTL = timedelta(hours=24)
_BOT_PRINCIPAL = "bot"


class _CachedToken(NamedTuple):
    token: str
    expires_at: str | None
    cached_at: datetime
    # Bot tokens only; empty means installation-wide.
    repositories: tuple[str, ...] = ()


# (thread_id, principal) -> token. Expired bot entries stay until the 24h cap so they
# can be re-minted at the same repository scope, never a wider one.
_GITHUB_TOKEN_CACHE: dict[tuple[str, str], _CachedToken] = {}


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
    repositories: Sequence[str] | None = None,
) -> None:
    """Cache a GitHub token in process for the current thread and principal.

    ``repositories`` is the scope a bot token was minted with (None: installation-wide).
    """
    if not thread_id or not token:
        return
    cache_principal = _BOT_PRINCIPAL if is_bot_token else principal
    if not cache_principal:
        logger.warning("Refusing to cache an unbound user GitHub token for thread %s", thread_id)
        return
    now = datetime.now(UTC)
    _GITHUB_TOKEN_CACHE[(thread_id, cache_principal)] = _CachedToken(
        token, expires_at, now, tuple(repositories or ()) if is_bot_token else ()
    )
    _evict_expired(now=now)


def _is_expired(expires_at: Any, *, now: datetime | None = None) -> bool:
    """Return True when ``expires_at`` is past (or close to) ``now``."""
    if expires_at is None:
        return False

    parsed: datetime | None = None
    if isinstance(expires_at, int | float):
        try:
            parsed = datetime.fromtimestamp(float(expires_at), tz=UTC)
        except OverflowError, OSError, ValueError:
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


def _past_max_ttl(entry: _CachedToken, *, now: datetime) -> bool:
    return now - entry.cached_at >= _GITHUB_TOKEN_MAX_TTL


def _entry_expired(entry: _CachedToken, *, now: datetime) -> bool:
    """Expired when past the token's own expiry or the 24h cache cap."""
    return _past_max_ttl(entry, now=now) or _is_expired(entry.expires_at, now=now)


def _evictable(key: tuple[str, str], entry: _CachedToken, *, now: datetime) -> bool:
    if key[1] == _BOT_PRINCIPAL:
        return _past_max_ttl(entry, now=now)
    return _entry_expired(entry, now=now)


def _evict_expired(*, now: datetime | None = None) -> None:
    current = now or datetime.now(UTC)
    stale = [
        key for key, entry in _GITHUB_TOKEN_CACHE.items() if _evictable(key, entry, now=current)
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
    now = datetime.now(UTC)
    for key in keys:
        cached = _GITHUB_TOKEN_CACHE.get(key)
        if not cached:
            continue
        if _entry_expired(cached, now=now):
            if _evictable(key, cached, now=now):
                _GITHUB_TOKEN_CACHE.pop(key, None)
            logger.info("Cached GitHub token for thread %s has expired; re-resolving", thread_id)
            continue
        return cached.token, cached.expires_at
    return None, None


def _thread_id_from_config(run_config: Mapping[str, Any]) -> str | None:
    return RunConfig.from_config(run_config).thread_id or None


def _principal_from_config(run_config: Mapping[str, Any]) -> str | None:
    cfg = RunConfig.from_config(run_config)
    return github_token_principal(login=cfg.github_login, email=cfg.user_email)


def get_github_token(run_config: Mapping[str, Any] | None = None) -> str | None:
    """Resolve the current thread's GitHub token from process memory."""
    resolved = run_config if run_config is not None else get_config()
    token, _expires_at = _cached_token_if_fresh(
        _thread_id_from_config(resolved), _principal_from_config(resolved)
    )
    return token


async def resolve_thread_github_token(run_config: Mapping[str, Any] | None = None) -> str | None:
    """Resolve the current thread's GitHub token, re-minting an expired bot token."""
    resolved = run_config if run_config is not None else get_config()
    if token := get_github_token(resolved):
        return token
    thread_id = _thread_id_from_config(resolved)
    expired_bot = _GITHUB_TOKEN_CACHE.get((thread_id, _BOT_PRINCIPAL)) if thread_id else None
    if not thread_id or expired_bot is None:
        return None
    repositories = expired_bot.repositories
    token, expires_at = await get_github_app_installation_token_with_expiry(
        repositories=list(repositories) or None
    )
    if not token:
        logger.warning("Could not re-mint expired bot GitHub token", extra={"thread_id": thread_id})
        return None
    logger.info("Re-minted expired bot GitHub token", extra={"thread_id": thread_id})
    cache_github_token_for_thread(
        thread_id, token, expires_at=expires_at, is_bot_token=True, repositories=repositories
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
