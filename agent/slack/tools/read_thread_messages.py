from typing import Any

from agent.slack.client import (
    SLACK_THREAD_MAX_MESSAGES,
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

    result = await _fetch_and_format(channel_id.strip(), message_ts.strip())
    if not result.get("success"):
        return {
            "success": False,
            "error": "Could not fetch thread messages. The bot may not have access to "
            "that channel, or the message may have been deleted.",
        }

    return result
