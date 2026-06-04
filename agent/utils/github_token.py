"""GitHub token lookup utilities."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
from langgraph.config import get_config
from langgraph_sdk import get_client

from ..encryption import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)

THREAD_GITHUB_TOKENS_NAMESPACE: list[str] = ["thread_github_tokens"]
_ENCRYPTED_TOKEN_KEY = "encrypted_token"
_TOKEN_EXPIRES_AT_KEY = "expires_at"


class GitHubAuthError(Exception):
    """Raised when a GitHub call returns 401, signalling a stale/revoked token."""


# Treat tokens with <= this many seconds remaining as expired so we re-auth
# before kicking off long agent runs.
_GITHUB_TOKEN_EXPIRY_SKEW_SECONDS = 60

client = get_client()


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


def _store_value(item: Any) -> dict[str, Any] | None:
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


async def _get_thread_token_record(thread_id: str) -> dict[str, Any] | None:
    try:
        item = await client.store.get_item(THREAD_GITHUB_TOKENS_NAMESPACE, thread_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise
    return _store_value(item) if item is not None else None


async def persist_github_token_for_thread(
    thread_id: str, token: str, expires_at: str | None = None
) -> str:
    """Encrypt a GitHub token into LangGraph Store for a thread."""
    encrypted = encrypt_token(token)
    await client.store.put_item(
        THREAD_GITHUB_TOKENS_NAMESPACE,
        thread_id,
        {
            _ENCRYPTED_TOKEN_KEY: encrypted,
            _TOKEN_EXPIRES_AT_KEY: expires_at,
            "updated_at": datetime.now(UTC).isoformat(),
        },
    )
    return encrypted


async def get_github_token_from_thread(
    thread_id: str,
) -> tuple[str | None, str | None, str | None]:
    """Resolve a GitHub token from LangGraph Store by thread id.

    Returns ``(None, None, None)`` when no token is cached or when the cached
    token's expiry has elapsed. On a fresh hit, returns the decrypted token, its
    ciphertext, and the persisted expiry (or ``None``).
    """
    try:
        record = await _get_thread_token_record(thread_id)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to fetch GitHub token store record for %s", thread_id)
        return None, None, None

    if not record:
        return None, None, None
    encrypted_token = record.get(_ENCRYPTED_TOKEN_KEY)
    if not isinstance(encrypted_token, str) or not encrypted_token:
        return None, None, None
    expires_at_raw = record.get(_TOKEN_EXPIRES_AT_KEY)
    if _is_expired(expires_at_raw):
        logger.info("Cached GitHub token for thread %s has expired; re-resolving", thread_id)
        return None, None, None

    token = _decrypt_github_token(encrypted_token)
    if token:
        logger.info("Found GitHub token in Store for thread %s", thread_id)
    expires_at = expires_at_raw if isinstance(expires_at_raw, str) else None
    return token, encrypted_token, expires_at


async def aget_github_token(run_config: Mapping[str, Any] | None = None) -> str | None:
    """Resolve a GitHub token from Store or the OAuth profile store."""
    resolved = run_config if run_config is not None else get_config()
    configurable = resolved.get("configurable", {})
    if not isinstance(configurable, Mapping):
        return None

    thread_id = configurable.get("thread_id")
    if configurable.get("review_requested") is True and isinstance(thread_id, str) and thread_id:
        token, _encrypted, _expires_at = await get_github_token_from_thread(thread_id)
        if token:
            return token

    source = configurable.get("source")
    github_login = configurable.get("github_login")
    if source in ("slack", "dashboard") and isinstance(github_login, str) and github_login.strip():
        from ..dashboard.profiles import get_valid_access_token

        token = await get_valid_access_token(github_login.strip())
        if token:
            return token

    if isinstance(thread_id, str) and thread_id:
        token, _encrypted, _expires_at = await get_github_token_from_thread(thread_id)
        return token
    return None


def get_github_token(run_config: Mapping[str, Any] | None = None) -> str | None:
    """Resolve a GitHub token for synchronous tools."""
    return asyncio.run(aget_github_token(run_config))


async def invalidate_cached_github_token(thread_id: str) -> None:
    """Clear a cached GitHub token from LangGraph Store."""
    try:
        await client.store.delete_item(THREAD_GITHUB_TOKENS_NAMESPACE, thread_id)
        logger.info("Invalidated cached GitHub token for thread %s", thread_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return
        logger.exception("Failed to invalidate cached GitHub token for thread %s", thread_id)
    except Exception:
        logger.exception("Failed to invalidate cached GitHub token for thread %s", thread_id)
