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


def dm_thread_title(name: str) -> str:
    """The fixed name of a person's DM thread, or "" until Slack tells us who they are.

    An empty title leaves the thread unnamed rather than naming it after one
    request, so the next message can still name it for the person.
    """
    return f"DMs between Open SWE and {name.strip()}" if name.strip() else ""
