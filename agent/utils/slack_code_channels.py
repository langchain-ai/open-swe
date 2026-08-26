"""Slack code channels, keyed with a non-message timestamp because the whole
channel is one agent session rather than a Slack thread.
"""

import logging
from typing import Any, Literal
from urllib.parse import quote

import httpx

from .http import DEFAULT_HTTP_TIMEOUT
from .slack import (
    SLACK_API_BASE_URL,
    SLACK_BOT_TOKEN,
    _slack_headers,
    get_slack_channel_info,
)

logger = logging.getLogger(__name__)

CODE_CHANNEL_SESSION_TS = "0"
VIEW_CONTENT_MAX_CHARS = 200_000


def is_code_channel_session(thread_ts: str | None) -> bool:
    """Return whether a Slack location refers to a code channel session."""
    return thread_ts == CODE_CHANNEL_SESSION_TS


async def is_code_channel(channel_id: str) -> bool:
    """Return whether a Slack channel is a code channel owned by an agent."""
    channel = await get_slack_channel_info(channel_id)
    properties = channel.get("properties") if isinstance(channel, dict) else None
    record = properties.get("record_channel") if isinstance(properties, dict) else None
    return isinstance(record, dict) and record.get("record_type") == "agent_channel"


async def _call(method: str, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    if not SLACK_BOT_TOKEN:
        return None, "missing_slack_bot_token"
    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as http_client:
        try:
            response = await http_client.post(
                f"{SLACK_API_BASE_URL}/{method}",
                headers=_slack_headers(),
                json=payload,
            )
            if response.status_code == 429:
                return None, "rate_limited"
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            logger.exception("Slack %s request failed", method)
            return None, f"http_error: {type(exc).__name__}"
        if not data.get("ok"):
            error = str(data.get("error") or "unknown_error")
            logger.warning("Slack %s failed: %s", method, error)
            return None, error
        return data, None


async def create_code_channel(
    *,
    name: str,
    session_id: str,
    origin_channel_id: str,
    origin_message_ts: str,
) -> tuple[str | None, str | None]:
    """Create a code channel for a task and return its channel id."""
    data, error = await _call(
        "agents.conversations.create",
        {
            "name": name.strip()[:200],
            "session_id": session_id,
            "origin_channel_id": origin_channel_id,
            "origin_message_ts": origin_message_ts,
        },
    )
    if error or data is None:
        return None, error
    channel = data.get("channel")
    channel_id = channel.get("id") if isinstance(channel, dict) else data.get("channel_id")
    if isinstance(channel_id, str) and channel_id:
        return channel_id, None
    return None, "missing_channel_id"


async def set_session_status(
    channel_id: str, status: Literal["processing", "active", "suspended", "closed"]
) -> bool:
    """Set a code channel's lifecycle status."""
    _, error = await _call(
        "agents.sessions.setStatus", {"channel_id": channel_id, "status": status}
    )
    return error is None


async def rename_session(channel_id: str, title: str) -> tuple[bool, str | None]:
    """Rename a code channel session."""
    _, error = await _call(
        "agents.sessions.rename",
        {"channel_id": channel_id, "title": title.strip()[:200]},
    )
    return error is None, error


async def set_context_bar(channel_id: str, items: list[dict[str, Any]]) -> tuple[bool, str | None]:
    """Pin up to five agent-supplied items to the top of a code channel."""
    _, error = await _call(
        "agents.conversations.setProperties",
        {
            "channel_id": channel_id,
            "code_channel": {"context_bar_items": items[:5]},
        },
    )
    return error is None, error


async def set_diff_view(
    channel_id: str,
    content: str,
    *,
    base_branch: str = "",
    head_branch: str = "",
) -> tuple[bool, str | None]:
    """Create or replace the diff tab on a code channel."""
    payload: dict[str, Any] = {
        "channel_id": channel_id,
        "type": "diff",
        "content": content[:VIEW_CONTENT_MAX_CHARS],
    }
    if base_branch:
        payload["base_branch"] = base_branch
    if head_branch:
        payload["head_branch"] = head_branch
    _, error = await _call("agents.conversations.setView", payload)
    return error is None, error


async def archive_code_channel(
    channel_id: str, *, summary_message_ts: str = ""
) -> tuple[bool, str | None]:
    """Archive a code channel, recording the agent's closing summary."""
    payload: dict[str, Any] = {"channel_id": channel_id}
    if summary_message_ts:
        payload["summary_message_ts"] = summary_message_ts
    _, error = await _call("agents.conversations.archive", payload)
    return error is None, error


def repo_context_bar_items(
    repo: dict[str, str] | None, *, branch: str = "", pr_url: str = ""
) -> list[dict[str, Any]]:
    """Build the standard repo/branch/PR context bar for an Open SWE session."""
    items: list[dict[str, Any]] = []
    owner = (repo or {}).get("owner", "")
    name = (repo or {}).get("name", "")
    if owner and name:
        items.append(
            {
                "key": "repo",
                "label": f"{owner}/{name}",
                "icon": "folder",
                "url": f"https://github.com/{owner}/{name}",
            }
        )
    if branch:
        item = {"key": "branch", "label": branch, "icon": "branch"}
        if owner and name:
            item["url"] = f"https://github.com/{owner}/{name}/tree/{quote(branch, safe='/')}"
        items.append(item)
    if pr_url.startswith("https://"):
        items.append({"key": "pr", "label": "Pull request", "icon": "link", "url": pr_url})
    return items
