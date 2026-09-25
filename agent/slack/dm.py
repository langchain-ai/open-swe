"""Bot DMs, optionally run in concierge mode: one conversation instead of a thread per message.

Off unless the person turns on ``concierge_mode`` in their own preferences. When
it is on, the conversation is keyed with the same non-message timestamp code
channels and incident channels use, so replies post into the DM instead of
opening a thread and the channel's own history is the conversation transcript. That
timestamp is also how the rest of the code recognizes the mode: only a DM the
owner enabled ever reaches a run with it.
"""

import logging

from agent.slack.client import lookup_slack_thread_id
from agent.slack.payloads import SlackChannelContext
from agent.users import User
from agent.utils.thread_ops import langgraph_client, queue_message_for_thread

logger = logging.getLogger(__name__)

CONCIERGE_TS = "0"


def is_dm_channel(channel_context: SlackChannelContext | None) -> bool:
    """Whether Slack reports this channel as a direct message with the bot."""
    return channel_context is not None and channel_context.is_im is True


def is_concierge_thread(channel_context: SlackChannelContext | None, thread_ts: str) -> bool:
    """Whether this location is a DM running in concierge mode."""
    return is_dm_channel(channel_context) and thread_ts == CONCIERGE_TS


def dm_thread_title(name: str) -> str:
    """The fixed name of a person's DM thread, or "" until Slack tells us who they are.

    An empty title leaves the thread unnamed rather than naming it after one
    request, so the next message can still name it for the person.
    """
    return f"DMs between Open SWE and {name.strip()}" if name.strip() else ""


async def note_for_concierge(slack_user_id: str, dm_channel_id: str, note: str) -> None:
    """Queue ``note`` for the person's concierge thread, which skips the bot's own DM posts."""
    if not await User.concierge_mode_for_slack(slack_user_id):
        return
    thread_id = await lookup_slack_thread_id(langgraph_client(), dm_channel_id, CONCIERGE_TS)
    if thread_id is None:
        return
    if not await queue_message_for_thread(thread_id, [{"type": "text", "text": note}]):
        logger.warning(
            "Could not queue a note for the concierge thread",
            extra={"slack_user_id": slack_user_id, "agent_thread_id": thread_id},
        )
