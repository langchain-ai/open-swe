"""Account links between a dashboard user (GitHub login) and external identities.

Each provider has its own namespace keyed by the provider's stable user ID so
that webhook resolution is a single ``get_item`` call. Dashboard reads use
``search_items`` with a Python-side filter — fine for an internal tool with a
small user set; revisit if this becomes hot.

OAuth access tokens are deliberately not persisted here: tokens are obtained
once during the link callback to verify the user's identity, then discarded.
We only keep the verified IDs and email.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from langgraph_sdk import get_client

logger = logging.getLogger(__name__)

Provider = Literal["slack", "linear"]

SLACK_LINKS_NAMESPACE: list[str] = ["account_links", "slack"]
LINEAR_LINKS_NAMESPACE: list[str] = ["account_links", "linear"]


def _client():
    return get_client()


def _namespace(provider: Provider) -> list[str]:
    if provider == "slack":
        return SLACK_LINKS_NAMESPACE
    return LINEAR_LINKS_NAMESPACE


async def _get_value(namespace: list[str], key: str) -> dict[str, Any] | None:
    try:
        item = await _client().store.get_item(namespace, key)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


async def upsert_slack_link(
    *,
    github_login: str,
    slack_user_id: str,
    slack_team_id: str,
    slack_email: str | None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "github_login": github_login,
        "slack_user_id": slack_user_id,
        "slack_team_id": slack_team_id,
        "slack_email": slack_email or "",
        "linked_at": datetime.now(UTC).isoformat(),
    }
    await _client().store.put_item(SLACK_LINKS_NAMESPACE, slack_user_id, value)
    return value


async def upsert_linear_link(
    *,
    github_login: str,
    linear_user_id: str,
    linear_workspace_id: str,
    linear_email: str | None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "github_login": github_login,
        "linear_user_id": linear_user_id,
        "linear_workspace_id": linear_workspace_id,
        "linear_email": linear_email or "",
        "linked_at": datetime.now(UTC).isoformat(),
    }
    await _client().store.put_item(LINEAR_LINKS_NAMESPACE, linear_user_id, value)
    return value


async def get_slack_link_by_user(slack_user_id: str) -> dict[str, Any] | None:
    if not slack_user_id:
        return None
    return await _get_value(SLACK_LINKS_NAMESPACE, slack_user_id)


async def get_linear_link_by_user(linear_user_id: str) -> dict[str, Any] | None:
    if not linear_user_id:
        return None
    return await _get_value(LINEAR_LINKS_NAMESPACE, linear_user_id)


async def _scan_namespace(namespace: list[str]) -> list[dict[str, Any]]:
    result = await _client().store.search_items(namespace, limit=1000)
    items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
    out: list[dict[str, Any]] = []
    for item in items or []:
        value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
        if isinstance(value, dict):
            out.append(value)
    return out


async def _find_link_for_login(provider: Provider, github_login: str) -> dict[str, Any] | None:
    if not github_login:
        return None
    for record in await _scan_namespace(_namespace(provider)):
        if record.get("github_login") == github_login:
            return record
    return None


async def get_links_for_login(github_login: str) -> dict[str, dict[str, Any] | None]:
    return {
        "slack": await _find_link_for_login("slack", github_login),
        "linear": await _find_link_for_login("linear", github_login),
    }


async def delete_link_for_login(provider: Provider, github_login: str) -> bool:
    record = await _find_link_for_login(provider, github_login)
    if not record:
        return False
    if provider == "slack":
        key = record.get("slack_user_id")
    else:
        key = record.get("linear_user_id")
    if not isinstance(key, str) or not key:
        return False
    try:
        await _client().store.delete_item(_namespace(provider), key)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return False
        raise
    return True
