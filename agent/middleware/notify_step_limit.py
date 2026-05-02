"""After-agent middleware that notifies users when the step limit is reached."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from langchain.agents.middleware import AgentState, after_agent
from langgraph.config import get_config
from langgraph.runtime import Runtime

from ..utils.slack import post_slack_thread_reply

logger = logging.getLogger(__name__)

_LIMIT_MARKER = "Model call limits exceeded"


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    parts: list[str] = []
    for block in content:
        if isinstance(block, Mapping):
            text = block.get("text", "")
            parts.append(text if isinstance(text, str) else str(text))
        else:
            parts.append(str(block))
    return " ".join(parts)


@after_agent
async def notify_step_limit_reached(
    state: AgentState,
    runtime: Runtime,
) -> dict[str, Any] | None:
    """Notify the user via Slack when the agent hits its step limit.

    Runs after the agent exits. Checks whether the last AI message contains
    the ``ModelCallLimitMiddleware`` marker text; if so, posts a Slack thread
    reply so the user is not left wondering what happened.
    """
    messages = state.get("messages", [])
    if not messages:
        return None

    last_msg = messages[-1]
    content = _content_to_text(getattr(last_msg, "content", "") or "")

    if _LIMIT_MARKER not in content:
        return None

    config = get_config()
    configurable = config.get("configurable", {})
    slack_thread = configurable.get("slack_thread") if isinstance(configurable, dict) else None
    if not isinstance(slack_thread, dict):
        logger.info("No Slack thread config — cannot send step-limit notification")
        return None

    channel_id = slack_thread.get("channel_id")
    thread_ts = slack_thread.get("thread_ts")

    if (
        not isinstance(channel_id, str)
        or not isinstance(thread_ts, str)
        or not channel_id
        or not thread_ts
    ):
        logger.info("No Slack thread config — cannot send step-limit notification")
        return None

    message = (
        "I've reached my maximum step limit and had to stop. "
        "The task may be incomplete. You can retry with a more focused request, "
        "or ask me to continue from where I left off."
    )

    try:
        await post_slack_thread_reply(channel_id, thread_ts, message)
        logger.info("Sent step-limit notification to Slack thread %s", thread_ts)
    except Exception:
        logger.exception("Failed to send step-limit notification")

    return None
