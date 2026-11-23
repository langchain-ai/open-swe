from openswe.prompts import prompt
from openswe.slack.channels import SlackChannel
from openswe.slack.client import (
    format_slack_messages_for_prompt,
    get_slack_user_names,
)
from openswe.tools import access as tool_access

DEFAULT_CHANNEL_MESSAGE_LIMIT = 30

_NOT_PUBLIC = (
    "Only public, internal channels can be read this way. The channel could not be verified "
    "as public and internal; it may be unavailable, private, a direct message, or shared "
    "with another organization. Ask the person for what you need instead."
)


class ChannelNotReadable(Exception):
    """The run may not read this channel with the bot's access on the asker's behalf."""


async def readable_public_channel(
    channel_id: str, current_access: tool_access.Access
) -> SlackChannel:
    """The public channel this run may read with the bot's own access, else ``ChannelNotReadable``."""
    if not current_access.private:
        if current_access.slack_own_channel is not None:
            if current_access.slack_own_channel != channel_id:
                raise ChannelNotReadable("This run can read only its own channel.")
        elif current_access.slack_public_channel is not None:
            destination = await SlackChannel.load(
                current_access.slack_public_channel, use_cache=False
            )
            if destination is None or not destination.public:
                raise ChannelNotReadable(prompt("slack/read-refused-shared-thread"))
        else:
            raise ChannelNotReadable(prompt("slack/read-refused-shared-thread"))
    channel = await SlackChannel.load(channel_id, use_cache=False)
    if channel is None or not channel.public:
        raise ChannelNotReadable(_NOT_PUBLIC)
    return channel


@tool_access.access(tool_access.Policy(trusted="anywhere"))
async def slack_read_channel_messages(channel_id: str, limit: int = 30) -> dict[str, object]:
    """Implement the `slack_read_channel_messages` tool."""
    if not channel_id or not channel_id.strip():
        return {"success": False, "error": "channel_id is required"}
    try:
        channel = await readable_public_channel(
            channel_id.strip(), await tool_access.resolve_access()
        )
    except ChannelNotReadable as exc:
        return {"success": False, "error": str(exc)}

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
    return {
        "success": True,
        "formatted": format_slack_messages_for_prompt(
            [message.dump() for message in messages], user_names, include_thread_replies=True
        ),
        "count": len(messages),
    }
