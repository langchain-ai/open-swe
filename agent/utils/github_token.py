"""GitHub token lookup utilities."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from langgraph.config import get_config
from langgraph_sdk import get_client
from langgraph_sdk.errors import NotFoundError

from ..encryption import decrypt_token

logger = logging.getLogger(__name__)

_GITHUB_TOKEN_METADATA_KEY = "github_token_encrypted"
_GITHUB_TOKEN_EXPIRES_AT_METADATA_KEY = "github_token_expires_at"


class GitHubAuthError(Exception):
    """Raised when a GitHub call returns 401, signalling a stale/revoked token."""


# Treat tokens with <= this many seconds remaining as expired so we re-auth
# before kicking off long agent runs.
_GITHUB_TOKEN_EXPIRY_SKEW_SECONDS = 60

client = get_client()


def _read_encrypted_github_token(metadata: dict[str, Any]) -> str | None:
    encrypted_token = metadata.get(_GITHUB_TOKEN_METADATA_KEY)
    return encrypted_token if isinstance(encrypted_token, str) and encrypted_token else None


def _decrypt_github_token(encrypted_token: str | None) -> str | None:
    if not encrypted_token:
        return None

    return decrypt_token(encrypted_token)


def _is_expired(expires_at: Any, *, now: datetime | None = None) -> bool:
    """Return True when ``expires_at`` is past (or close to) ``now``.

    Accepts ISO-8601 strings (with or without trailing Z) and unix timestamps.
    Unparseable values are treated as not expired so we don't break callers
    that haven't started persisting an expiry yet.
    """
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


def _read_token_if_fresh(metadata: dict[str, Any]) -> str | None:
    """Decrypt the cached token only if it has not expired."""
    encrypted = _read_encrypted_github_token(metadata)
    if not encrypted:
        return None
    if _is_expired(metadata.get(_GITHUB_TOKEN_EXPIRES_AT_METADATA_KEY)):
        return None
    return _decrypt_github_token(encrypted)


def get_github_token(run_config: Mapping[str, Any] | None = None) -> str | None:
    """Resolve a GitHub token from run metadata.

    Pass ``run_config`` when LangGraph runnable config is already available (e.g. after
    ``get_config()`` in callers). Omit to read from ``get_config()`` (required runnable
    context). Returns ``None`` for tokens whose ``github_token_expires_at`` is past.
    """
    resolved = run_config if run_config is not None else get_config()
    return _read_token_if_fresh(resolved.get("metadata", {}))


async def get_github_token_from_thread(thread_id: str) -> tuple[str | None, str | None]:
    """Resolve a GitHub token from LangGraph thread metadata.

    Returns ``(None, None)`` when no token is cached or when the cached token's
    ``github_token_expires_at`` has elapsed — callers must treat the cache as
    missing in that case and re-resolve.
    """
    try:
        thread = await client.threads.get(thread_id)
    except NotFoundError:
        logger.debug("Thread %s not found while looking up GitHub token", thread_id)
        return None, None
    except Exception:  # noqa: BLE001
        logger.exception("Failed to fetch thread metadata for %s", thread_id)
        return None, None

    metadata = (thread or {}).get("metadata", {})
    encrypted_token = _read_encrypted_github_token(metadata)
    if not encrypted_token:
        return None, None
    if _is_expired(metadata.get(_GITHUB_TOKEN_EXPIRES_AT_METADATA_KEY)):
        logger.info("Cached GitHub token for thread %s has expired; re-resolving", thread_id)
        return None, None

    token = _decrypt_github_token(encrypted_token)
    if token:
        logger.info("Found GitHub token in thread metadata for thread %s", thread_id)
    return token, encrypted_token


async def invalidate_cached_github_token(thread_id: str) -> None:
    """Clear a cached GitHub token from thread metadata.

    Called when a downstream GitHub API call returns 401, so the next run
    re-resolves a fresh token instead of replaying the revoked ciphertext.
    """
    try:
        await client.threads.update(
            thread_id=thread_id,
            metadata={
                _GITHUB_TOKEN_METADATA_KEY: None,
                _GITHUB_TOKEN_EXPIRES_AT_METADATA_KEY: None,
            },
        )
        logger.info("Invalidated cached GitHub token for thread %s", thread_id)
    except Exception:
        logger.exception("Failed to invalidate cached GitHub token for thread %s", thread_id)
