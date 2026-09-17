from typing import Any

from agent.slack.client import (
    SLACK_CHANNEL_HISTORY_MAX_MESSAGES,
    fetch_slack_channel_messages,
    format_slack_messages_for_prompt,
    get_slack_user_names,
    slack_channel_is_public,
)

DEFAULT_CHANNEL_MESSAGE_LIMIT = 30

_NOT_PUBLIC = (
    "Only public channels can be read this way. That channel is private, a direct message, "
    "or shared with another organization, and reading it here would bypass its membership. "
    "Ask the person for what you need instead."
)


async def slack_read_channel_messages(channel_id: str, limit: int = 30) -> dict[str, Any]:
    """Implement the `slack_read_channel_messages` tool."""
    if not channel_id or not channel_id.strip():
        return {"success": False, "error": "channel_id is required"}
    if not await slack_channel_is_public(channel_id.strip()):
        return {"success": False, "error": _NOT_PUBLIC}

    requested = limit if isinstance(limit, int) and limit > 0 else DEFAULT_CHANNEL_MESSAGE_LIMIT
    messages = await fetch_slack_channel_messages(
        channel_id.strip(), min(requested, SLACK_CHANNEL_HISTORY_MAX_MESSAGES)
    )
    if not messages:
        return {
            "success": False,
            "error": "Could not read that channel. The bot may not be a member, or the "
            "channel may have no messages.",
        }

    user_ids = [
        user_id for msg in messages if isinstance(user_id := msg.get("user"), str) and user_id
    ]
    user_names = await get_slack_user_names(user_ids) if user_ids else {}
    return {
        "success": True,
        "formatted": format_slack_messages_for_prompt(
            messages, user_names, include_thread_replies=True
        ),
        "count": len(messages),
    }
