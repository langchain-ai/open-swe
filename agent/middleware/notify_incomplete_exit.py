"""After-agent middleware that notifies users when the graph exits without a final response."""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentState, after_agent
from langchain_core.messages import AnyMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime

from ..utils.slack import post_slack_thread_reply
from .ensure_no_empty_msg import check_if_model_messaged_user, get_every_message_since_last_human

logger = logging.getLogger(__name__)

_FALLBACK_MESSAGE = (
    "Agent work was interrupted before I could finish. "
    "The task may be incomplete — see the trace for details."
)


def _last_message_is_tool(messages: list[AnyMessage]) -> bool:
    if not messages:
        return False
    return messages[-1].type == "tool"


@after_agent
async def notify_incomplete_exit(
    state: AgentState,
    runtime: Runtime,
) -> dict[str, Any] | None:
    """Notify the user via Slack when the agent exits without a user-facing response.

    Runs after the agent exits. If the graph terminated immediately after a
    tool call (last message is a ``ToolMessage`` with no subsequent AI
    inference) and no user-facing notification was sent since the last human
    message, post a fallback Slack reply so the thread isn't left silent.
    """
    messages = state.get("messages", [])
    if not _last_message_is_tool(messages):
        return None

    messages_since_last_human = get_every_message_since_last_human(state)
    if check_if_model_messaged_user(messages_since_last_human):
        return None

    last_tool_name = getattr(messages[-1], "name", None)
    logger.warning(
        "Agent graph exited after tool '%s' without a final AI response or user notification",
        last_tool_name,
    )

    config = get_config()
    configurable = config.get("configurable", {})
    slack_thread = configurable.get("slack_thread") if isinstance(configurable, dict) else None
    if not isinstance(slack_thread, dict):
        logger.info("No Slack thread config — cannot send incomplete-exit notification")
        return None

    channel_id = slack_thread.get("channel_id")
    thread_ts = slack_thread.get("thread_ts")

    if (
        not isinstance(channel_id, str)
        or not isinstance(thread_ts, str)
        or not channel_id
        or not thread_ts
    ):
        logger.info("No Slack thread config — cannot send incomplete-exit notification")
        return None

    try:
        await post_slack_thread_reply(channel_id, thread_ts, _FALLBACK_MESSAGE)
        logger.info("Sent incomplete-exit notification to Slack thread %s", thread_ts)
    except Exception:
        logger.exception("Failed to send incomplete-exit notification")

    return None
