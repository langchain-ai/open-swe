"""Hand background events that need action to the agent that owns a Slack thread."""

import logging
import time

from openswe.input_messages import SystemIdentity
from openswe.prompts import prompt
from openswe.slack import webhook
from openswe.slack.channels import SlackChannel
from openswe.slack.client import resolve_slack_thread_id
from openswe.slack.request import SlackRequest
from openswe.utils.thread_ops import langgraph_client
from openswe.webhooks import common

logger = logging.getLogger(__name__)
_BACKGROUND_EVENT: SystemIdentity = {
    "id": "system:background-event",
    "display_name": "Open SWE background event",
    "platform": "open-swe",
}


class ThreadOwnerWakeError(Exception):
    """The Slack thread's agent was not started, so nothing will act on the event."""


async def wake_thread_owner(channel_id: str, thread_ts: str, slack_user_id: str, event: str) -> str:
    """Start a run for the thread's owning agent, acting for ``slack_user_id``, about ``event``."""
    thread_id = await resolve_slack_thread_id(langgraph_client(), channel_id, thread_ts)
    channel_context = await SlackChannel.context_for(channel_id)
    repo = await common.get_slack_repo_config(
        channel_id,
        thread_ts,
        slack_user_id=slack_user_id,
        channel_context=channel_context,
        thread_id=thread_id,
    )
    logger.info(
        "Handing a background event to the Slack thread's agent",
        extra={
            "slack_channel": channel_id,
            "slack_thread_ts": thread_ts,
            "agent_thread_id": thread_id,
        },
    )
    started = await webhook.start_slack_run(
        SlackRequest(
            channel_id=channel_id,
            channel_context=channel_context,
            thread_ts=thread_ts,
            # After every message in the thread, so the agent reads all of them first.
            event_ts=f"{time.time():.6f}",
            user_id=slack_user_id,
            trigger_system=_BACKGROUND_EVENT,
            text=prompt("slack/thread-owner-event", event=event),
            bot_user_id=common.SLACK_BOT_USER_ID,
            thread_id=thread_id,
        ),
        repo,
    )
    if not started:
        raise ThreadOwnerWakeError(f"No run started for the agent of Slack thread {thread_ts}")
    return thread_id
