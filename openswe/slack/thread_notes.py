"""Informational events for a Slack thread's agent, queued for its next run instead of starting one."""

import logging

from openswe.slack.client import lookup_slack_thread_id
from openswe.utils.thread_ops import langgraph_client, queue_message_for_thread

logger = logging.getLogger(__name__)


async def note_for_thread_owner(channel_id: str, thread_ts: str, note: str) -> None:
    """Queue ``note`` for the thread's owning agent, if it has one, without starting a run."""
    thread_id = await lookup_slack_thread_id(langgraph_client(), channel_id, thread_ts)
    if thread_id is None:
        return
    if not await queue_message_for_thread(thread_id, [{"type": "text", "text": note}]):
        logger.warning(
            "Could not queue a note for the Slack thread's agent",
            extra={"slack_channel": channel_id, "agent_thread_id": thread_id},
        )
