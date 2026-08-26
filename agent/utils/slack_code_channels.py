"""Slack code channels — ``agents.conversations.*`` and ``agents.sessions.*``.

A code channel is one Slack channel dedicated to one agent session: the whole
channel is the session, so there is no thread timestamp to key it by. Open SWE
keys every Slack location by ``(channel_id, thread_ts)``, so code channels use
:data:`CODE_CHANNEL_SESSION_TS` as their timestamp — it satisfies the existing
timestamp validation and can never collide with a real Slack message ts.
"""

import logging
from typing import Any, Literal

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
CODE_CHANNEL_RECORD_TYPE = "agent_channel"
CONTEXT_BAR_MAX_ITEMS = 5
VIEW_CONTENT_MAX_CHARS = 200_000
CHANNEL_NAME_MAX_CHARS = 80

SessionStatus = Literal["processing", "active", "suspended", "closed"]
ViewType = Literal["html", "diff", "block_kit", "canvas"]


def is_code_channel_session(thread_ts: str | None) -> bool:
    """Return whether a Slack location refers to a code channel session."""
    return thread_ts == CODE_CHANNEL_SESSION_TS


async def is_code_channel(channel_id: str) -> bool:
    """Return whether a Slack channel is a code channel owned by an agent."""
    channel = await get_slack_channel_info(channel_id)
    properties = channel.get("properties") if isinstance(channel, dict) else None
    record = properties.get("record_channel") if isinstance(properties, dict) else None
    return isinstance(record, dict) and record.get("record_type") == CODE_CHANNEL_RECORD_TYPE


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
            "name": name.strip()[:CHANNEL_NAME_MAX_CHARS],
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
    channel_id: str, status: SessionStatus, *, thread_ts: str | None = None
) -> bool:
    """Set the lifecycle status of a code channel or thread session."""
    payload: dict[str, Any] = {"channel_id": channel_id, "status": status}
    if thread_ts and not is_code_channel_session(thread_ts):
        payload["thread_ts"] = thread_ts
    _, error = await _call("agents.sessions.setStatus", payload)
    return error is None


async def rename_session(channel_id: str, title: str) -> tuple[bool, str | None]:
    """Rename a code channel session."""
    _, error = await _call(
        "agents.sessions.rename",
        {"channel_id": channel_id, "title": title.strip()[:CHANNEL_NAME_MAX_CHARS]},
    )
    return error is None, error


async def set_context_bar(channel_id: str, items: list[dict[str, Any]]) -> tuple[bool, str | None]:
    """Pin up to five agent-supplied items to the top of a code channel."""
    _, error = await _call(
        "agents.conversations.setProperties",
        {
            "channel_id": channel_id,
            "code_channel": {"context_bar_items": items[:CONTEXT_BAR_MAX_ITEMS]},
        },
    )
    return error is None, error


async def set_view(
    channel_id: str,
    view_type: ViewType,
    content: str,
    *,
    title: str = "",
    base_branch: str = "",
    head_branch: str = "",
) -> tuple[bool, str | None]:
    """Create or replace a view tab on a code channel."""
    payload: dict[str, Any] = {
        "channel_id": channel_id,
        "type": view_type,
        "content": content[:VIEW_CONTENT_MAX_CHARS],
    }
    for key, value in (
        ("title", title),
        ("base_branch", base_branch),
        ("head_branch", head_branch),
    ):
        if value:
            payload[key] = value
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
        items.append({"key": "branch", "label": branch, "icon": "git-branch"})
    if pr_url.startswith("https://"):
        items.append({"key": "pr", "label": "Pull request", "icon": "link", "url": pr_url})
    return items
