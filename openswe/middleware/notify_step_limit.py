"""After-agent middleware that notifies users when the step limit is reached."""

import logging

from langgraph_sdk import get_client

from openswe.run_config import RunConfig
from openswe.slack.client import (
    LANGGRAPH_URL,
    get_active_slack_thread,
    post_slack_thread_reply,
)
from openswe.utils.user_messages import warning

logger = logging.getLogger(__name__)

_LIMIT_MARKER = "Model call limits exceeded"


async def notify_step_limit() -> None:
    """Tell the run's Slack thread the run stopped at its step limit, as the run."""
    cfg = RunConfig.from_runtime()
    active = await get_active_slack_thread(
        get_client(url=LANGGRAPH_URL),
        cfg.thread_id,
        cfg.slack_thread.dump() if cfg.slack_thread else None,
    )
    if not active:
        logger.info("No Slack thread config — cannot send step-limit notification")
        return

    channel_id = active.get("channel_id")
    thread_ts = active.get("thread_ts")

    if (
        not isinstance(channel_id, str)
        or not isinstance(thread_ts, str)
        or not channel_id
        or not thread_ts
    ):
        logger.info("No Slack thread config — cannot send step-limit notification")
        return

    message = warning(
        "Open SWE reached its maximum step limit and had to stop. "
        "The task may be incomplete. You can retry with a more focused request, "
        "or ask it to continue from where it left off."
    )

    try:
        await post_slack_thread_reply(
            channel_id,
            thread_ts,
            message,
            agent_thread_id=cfg.thread_id,
        )
        logger.info("Sent step-limit notification to Slack thread %s", thread_ts)
    except Exception:
        logger.exception("Failed to send step-limit notification")
