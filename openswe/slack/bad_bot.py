"""The `/badbot` embedded command: record negative feedback on a Slack thread's agent thread."""

import logging

from openswe.slack.client import (
    add_slack_reaction,
    lookup_slack_thread_id,
    post_slack_ephemeral_message,
)
from openswe.thread_feedback import record_thread_feedback
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

RECORDED_REACTION = "memo"


async def process_slack_bad_bot(
    channel_id: str, thread_ts: str, message_ts: str, user_id: str, comment: str
) -> None:
    try:
        thread_id = await lookup_slack_thread_id(langgraph_client(), channel_id, thread_ts)
        if not thread_id:
            await post_slack_ephemeral_message(
                channel_id,
                user_id,
                "There is no Open SWE thread here to give feedback on. "
                "Use `/badbot` in a thread Open SWE worked in.",
                thread_ts,
            )
            return
        await record_thread_feedback(
            thread_id,
            "bad",
            comment[:3000],
            event_id=f"slack_badbot:{channel_id}:{message_ts}",
            run_id="",
            source="slack",
            source_info={
                "source": "slack_badbot",
                "channel_id": channel_id,
                "message_ts": message_ts,
                "user_id": user_id,
            },
            slack_user_id=user_id,
        )
        await add_slack_reaction(channel_id, message_ts, RECORDED_REACTION)
    except Exception:
        logger.exception(
            "Could not record /badbot feedback", extra={"slack_channel_id": channel_id}
        )
