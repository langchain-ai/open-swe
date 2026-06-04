"""GitHub token lookup utilities."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from langgraph.config import get_config

logger = logging.getLogger(__name__)

# Treat tokens with <= this many seconds remaining as expired so we re-auth
# before kicking off long agent runs.
_GITHUB_TOKEN_EXPIRY_SKEW_SECONDS = 60
_GITHUB_TOKEN_CACHE: dict[str, tuple[str, str | None]] = {}


class GitHubAuthError(Exception):
    """Raised when a GitHub call returns 401, signalling a stale/revoked token."""


def cache_github_token_for_thread(
    thread_id: str, token: str, expires_at: str | None = None
) -> None:
    """Cache a GitHub token in process for the current thread."""
    if not thread_id or not token:
        return
    _GITHUB_TOKEN_CACHE[thread_id] = (token, expires_at)


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


def _cached_token_if_fresh(thread_id: str | None) -> tuple[str | None, str | None]:
    if not thread_id:
        return None, None
    cached = _GITHUB_TOKEN_CACHE.get(thread_id)
    if not cached:
        return None, None
    token, expires_at = cached
    if _is_expired(expires_at):
        _GITHUB_TOKEN_CACHE.pop(thread_id, None)
        logger.info("Cached GitHub token for thread %s has expired; re-resolving", thread_id)
        return None, None
    return token, expires_at


def _thread_id_from_config(run_config: Mapping[str, Any]) -> str | None:
    configurable = run_config.get("configurable", {})
    if not isinstance(configurable, Mapping):
        return None
    thread_id = configurable.get("thread_id")
    return thread_id if isinstance(thread_id, str) and thread_id else None


def get_github_token(run_config: Mapping[str, Any] | None = None) -> str | None:
    """Resolve the current thread's GitHub token from process memory."""
    resolved = run_config if run_config is not None else get_config()
    token, _expires_at = _cached_token_if_fresh(_thread_id_from_config(resolved))
    return token


async def get_github_token_from_thread(thread_id: str) -> tuple[str | None, str | None]:
    """Resolve the current process's cached GitHub token for a thread."""
    return _cached_token_if_fresh(thread_id)


async def invalidate_cached_github_token(thread_id: str) -> None:
    """Clear a cached GitHub token for a thread."""
    _GITHUB_TOKEN_CACHE.pop(thread_id, None)
    logger.info("Invalidated cached GitHub token for thread %s", thread_id)
