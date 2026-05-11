"""Cross-surface identity map keyed by canonical (lower-cased) email.

The map is persisted to the LangGraph store under namespace
``("user_identity_map",)`` so it survives process restarts. An in-process dict
mirrors the store for read latency.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from langgraph_sdk import get_client

logger = logging.getLogger(__name__)

_NAMESPACE: tuple[str, ...] = ("user_identity_map",)

_LANGGRAPH_URL = os.environ.get("LANGGRAPH_URL") or os.environ.get(
    "LANGGRAPH_URL_PROD", "http://localhost:2024"
)

# email -> row
_CACHE: dict[str, dict[str, Any]] = {}


def _canonical_email(email: str) -> str:
    return email.strip().lower()


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _empty_row(email: str) -> dict[str, Any]:
    return {
        "email": email,
        "github_logins": [],
        "slack_user_ids": [],
        "linear_user_ids": [],
        "last_seen": {},
    }


def _merge_unique(existing: list[str], value: str) -> list[str]:
    if value in existing:
        return existing
    return [*existing, value]


async def _load_row(email: str) -> dict[str, Any] | None:
    if email in _CACHE:
        return _CACHE[email]
    try:
        client = get_client(url=_LANGGRAPH_URL)
        item = await client.store.get_item(_NAMESPACE, email)
    except Exception:
        logger.exception("Failed to load identity row for %s", email)
        return None
    if not item:
        return None
    value = item.get("value") if isinstance(item, dict) else None
    if not isinstance(value, dict):
        return None
    _CACHE[email] = value
    return value


async def _save_row(email: str, row: dict[str, Any]) -> None:
    _CACHE[email] = row
    try:
        client = get_client(url=_LANGGRAPH_URL)
        await client.store.put_item(_NAMESPACE, email, row)
    except Exception:
        logger.exception("Failed to persist identity row for %s", email)


async def upsert_identity(
    email: str,
    *,
    github_login: str | None = None,
    slack_user_id: str | None = None,
    linear_user_id: str | None = None,
    surface: str,
) -> dict[str, Any] | None:
    """Merge the given identity into the row keyed by ``email``."""
    if not email:
        return None
    canonical = _canonical_email(email)
    if not canonical:
        return None

    row = await _load_row(canonical) or _empty_row(canonical)

    if github_login:
        row["github_logins"] = _merge_unique(row.get("github_logins", []), github_login)
    if slack_user_id:
        row["slack_user_ids"] = _merge_unique(row.get("slack_user_ids", []), slack_user_id)
    if linear_user_id:
        row["linear_user_ids"] = _merge_unique(row.get("linear_user_ids", []), linear_user_id)

    last_seen = row.get("last_seen") or {}
    last_seen[surface] = _now_iso()
    row["last_seen"] = last_seen
    row["email"] = canonical

    await _save_row(canonical, row)
    return row


async def get_identities_for_github_login(github_login: str) -> dict | None:
    """Return the identity row whose ``github_logins`` contains ``github_login``."""
    if not github_login:
        return None

    for row in _CACHE.values():
        if github_login in row.get("github_logins", []):
            return row

    try:
        client = get_client(url=_LANGGRAPH_URL)
        items = await client.store.search_items(_NAMESPACE, limit=1000)
    except Exception:
        logger.exception("Failed to search identity map for %s", github_login)
        return None

    rows: list[dict[str, Any]] = []
    if isinstance(items, dict):
        rows = [
            it.get("value")
            for it in items.get("items", [])
            if isinstance(it, dict) and isinstance(it.get("value"), dict)
        ]
    elif isinstance(items, list):
        for it in items:
            value = it.get("value") if isinstance(it, dict) else None
            if isinstance(value, dict):
                rows.append(value)

    for row in rows:
        email = row.get("email")
        if isinstance(email, str):
            _CACHE[email] = row
        if github_login in row.get("github_logins", []):
            return row
    return None
