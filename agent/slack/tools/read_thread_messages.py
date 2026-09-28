import re
from typing import Any

from agent.slack.client import (
    SLACK_THREAD_MAX_MESSAGES,
    fetch_slack_thread_messages,
    format_slack_messages_for_prompt,
    get_slack_user_names,
)

_SLACK_MESSAGE_TS_RE = re.compile(r"^\d{9,11}\.\d{6}$")
_INVALID_MESSAGE_TS_ERROR = (
    "message_ts must be a Slack message timestamp taken from an actual message (for example "
    "1790591577.851239), not a converted wall-clock time. Find the message first — use "
    "slack_read_channel_messages to list a channel's top-level messages and copy the thread_ts "
    "it reports."
)


async def _fetch_and_format(channel_id: str, message_ts: str) -> dict[str, Any]:
    """Fetch thread messages and resolve author names."""
    messages = await fetch_slack_thread_messages(channel_id, message_ts)
    fetch_error = getattr(messages, "error", None)
    if fetch_error == "access":
        return {
            "success": False,
            "error": "The bot cannot access or is not a member of that Slack channel.",
        }
    if fetch_error == "not_found":
        return {
            "success": False,
            "error": "The Slack channel is readable, but the requested thread does not exist.",
        }
    if fetch_error == "transport":
        return {"success": False, "error": "Slack returned an error while fetching the thread."}
    if not messages:
        return {"success": False, "messages": []}

    user_ids = [
        user_id for msg in messages if isinstance(user_id := msg.get("user"), str) and user_id
    ]
    user_names = await get_slack_user_names(user_ids) if user_ids else {}

    truncated = len(messages) >= SLACK_THREAD_MAX_MESSAGES
    formatted = format_slack_messages_for_prompt(messages, user_names)
    if truncated:
        formatted = (
            f"[thread truncated — showing most recent {len(messages)} messages]\n{formatted}"
        )
    return {
        "success": True,
        "formatted": formatted,
        "count": len(messages),
        "truncated": truncated,
    }


async def slack_read_thread_messages(channel_id: str, message_ts: str) -> dict[str, Any]:
    """Implement the `slack_read_thread_messages` tool."""
    if not channel_id or not channel_id.strip():
        return {"success": False, "error": "channel_id is required"}
    if not message_ts or not message_ts.strip():
        return {"success": False, "error": "message_ts is required"}
    message_ts = message_ts.strip()
    if not _SLACK_MESSAGE_TS_RE.fullmatch(message_ts) or message_ts.endswith(".000000"):
        return {"success": False, "error": _INVALID_MESSAGE_TS_ERROR}

    result = await _fetch_and_format(channel_id.strip(), message_ts)
    if not result.get("success"):
        return result

    return result
