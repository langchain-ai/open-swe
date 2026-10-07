"""Hand background events to the agent that owns a Slack thread instead of posting there."""

import logging

from agent.prompts import prompt
from agent.slack import webhook
from agent.slack.channels import SlackChannel
from agent.slack.client import lookup_slack_thread_id, resolve_slack_thread_id
from agent.slack.request import SlackRequest
from agent.utils.thread_ops import langgraph_client, queue_message_for_thread
from agent.webhooks import common

logger = logging.getLogger(__name__)


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
    await webhook.process_slack_mention(
        SlackRequest(
            channel_id=channel_id,
            channel_context=channel_context,
            thread_ts=thread_ts,
            user_id=slack_user_id,
            text=prompt("slack/thread-owner-event", event=event),
            bot_user_id=common.SLACK_BOT_USER_ID,
            thread_id=thread_id,
        ),
        repo,
    )
    return thread_id


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
