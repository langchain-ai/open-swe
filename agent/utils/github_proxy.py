"""Track and refresh the GitHub App token baked into a sandbox's proxy.

The LangSmith sandbox proxy is configured once at run start with a GitHub App
installation token. Those tokens expire after exactly one hour, so any agent
run longer than ~1h would start seeing 401s on every ``gh``/``git`` call in the
sandbox. This module records when each thread's proxy token expires and lets a
before-model middleware re-configure the proxy before it goes stale.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from .github_app import get_github_app_installation_token_with_expiry
from .sandbox_state import SANDBOX_BACKENDS, unwrap_sandbox_backend

logger = logging.getLogger(__name__)

# Refresh the proxy token once it is within this window of expiring.
PROXY_TOKEN_REFRESH_WINDOW = timedelta(minutes=5)
# Used only when the token's own expiry is unknown: refresh after this age.
PROXY_TOKEN_FALLBACK_TTL = timedelta(minutes=50)

# thread_id -> (token_expires_at | None, recorded_at)
_PROXY_TOKEN_EXPIRY: dict[str, tuple[datetime | None, datetime]] = {}


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


def record_proxy_token_expiry(thread_id: str | None, expires_at: Any) -> None:
    """Record when the proxy token configured for ``thread_id`` expires."""
    if not thread_id:
        return
    _PROXY_TOKEN_EXPIRY[thread_id] = (_parse_expiry(expires_at), datetime.now(UTC))


def clear_proxy_token_expiry(thread_id: str | None) -> None:
    if thread_id:
        _PROXY_TOKEN_EXPIRY.pop(thread_id, None)


def proxy_token_needs_refresh(thread_id: str | None, *, now: datetime | None = None) -> bool:
    """Whether the recorded proxy token is at/near expiry and should be refreshed."""
    if not thread_id:
        return False
    record = _PROXY_TOKEN_EXPIRY.get(thread_id)
    if record is None:
        return False
    expires_at, recorded_at = record
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if expires_at is not None:
        return (expires_at - current) <= PROXY_TOKEN_REFRESH_WINDOW
    return (current - recorded_at) >= PROXY_TOKEN_FALLBACK_TTL


async def maybe_refresh_proxy_token(thread_id: str | None, *, now: datetime | None = None) -> bool:
    """Re-configure the sandbox proxy with a fresh token when near expiry.

    Returns True when a refresh was performed. Only applies to LangSmith
    sandboxes; other providers don't use the proxy.
    """
    if os.getenv("SANDBOX_TYPE", "langsmith") != "langsmith":
        return False
    if not thread_id or not proxy_token_needs_refresh(thread_id, now=now):
        return False

    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    if sandbox_backend is None:
        return False

    token, expires_at = await get_github_app_installation_token_with_expiry()
    if not token:
        logger.warning(
            "Proxy token for thread %s is near expiry but no installation token is available",
            thread_id,
        )
        return False

    from ..integrations.langsmith import _configure_github_proxy

    current_backend = unwrap_sandbox_backend(sandbox_backend)
    await asyncio.to_thread(_configure_github_proxy, current_backend.id, token)
    record_proxy_token_expiry(thread_id, expires_at)
    logger.info("Refreshed GitHub proxy token for thread %s before expiry", thread_id)
    return True
