from typing import Any

from agent.slack.channel_config import SlackChannelConfig
from agent.slack.channels import SlackChannel
from agent.slack.client import (
    format_slack_messages_for_prompt,
    get_slack_user_names,
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
    channel = await SlackChannel.load(channel_id.strip())
    if channel is None or not channel.public:
        return {"success": False, "error": _NOT_PUBLIC}

    requested = limit if isinstance(limit, int) and limit > 0 else DEFAULT_CHANNEL_MESSAGE_LIMIT
    messages = await channel.messages(requested)
    if not messages:
        return {
            "success": False,
            "error": "Could not read that channel. The bot may not be a member, or the "
            "channel may have no messages.",
        }

    user_ids = [message.user for message in messages if message.user]
    user_names = await get_slack_user_names(user_ids) if user_ids else {}
    result: dict[str, Any] = {
        "success": True,
        "formatted": format_slack_messages_for_prompt(
            [message.dump() for message in messages], user_names, include_thread_replies=True
        ),
        "count": len(messages),
    }
    if instructions := await SlackChannelConfig.instructions_for(channel.id):
        result["channel_instructions"] = instructions
    return result
