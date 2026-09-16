"""Bot DMs, where the whole conversation is one private session rather than a Slack thread.

Keyed with the same non-message timestamp code channels and incident channels
use, so replies post into the conversation instead of opening a thread and the
channel's own history is the session transcript.
"""

from agent.slack.client import SlackChannelContext

DM_SESSION_TS = "0"


def is_dm_channel(channel_context: SlackChannelContext | None) -> bool:
    """Whether Slack reports this channel as a direct message with the bot."""
    return channel_context is not None and channel_context.get("is_im") is True
