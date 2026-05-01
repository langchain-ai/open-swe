import asyncio
from typing import Any

from ..utils.slack import (
    fetch_slack_thread_messages,
    format_slack_messages_for_prompt,
    get_slack_user_names,
)


async def _fetch_and_format(channel_id: str, message_ts: str) -> dict[str, Any]:
    """Fetch thread messages and resolve author names."""
    messages = await fetch_slack_thread_messages(channel_id, message_ts)
    if not messages:
        return {"success": False, "messages": []}

    user_ids = [
        msg.get("user") for msg in messages if isinstance(msg.get("user"), str) and msg.get("user")
    ]
    user_names = await get_slack_user_names(user_ids) if user_ids else {}

    formatted = format_slack_messages_for_prompt(messages, user_names)
    return {"success": True, "formatted": formatted, "count": len(messages)}


def slack_read_thread_messages(channel_id: str, message_ts: str) -> dict[str, Any]:
    """Read messages from a Slack thread.

    Use this tool to read messages from a Slack channel or thread.
    Provide the channel_id and message_ts (thread timestamp) to fetch all
    messages in that thread.

    If you encounter a Slack message URL like
    https://workspace.slack.com/archives/C0AME1J0/p1776281321762829
    you can extract the channel_id (C0AME1J0) and convert the timestamp
    by inserting a dot 6 digits from the end (1776281321.762829).

    Returns formatted thread messages with author names."""
    if not channel_id or not channel_id.strip():
        return {"success": False, "error": "channel_id is required"}
    if not message_ts or not message_ts.strip():
        return {"success": False, "error": "message_ts is required"}

    result = asyncio.run(_fetch_and_format(channel_id.strip(), message_ts.strip()))
    if not result.get("success"):
        return {
            "success": False,
            "error": "Could not fetch thread messages. The bot may not have access to "
            "that channel, or the message may have been deleted.",
        }

    return result
