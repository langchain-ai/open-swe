"""After-model middleware that posts AI responses to Linear.

Posts the first AI text response back to the originating Linear issue so
stakeholders can see progress without leaving Linear.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import after_model
from langgraph.config import get_config
from langgraph.runtime import Runtime

from .check_message_queue import LinearNotifyState

logger = logging.getLogger(__name__)

MIN_MESSAGES_FOR_PREV_CHECK = 2


@after_model(state_schema=LinearNotifyState)
async def post_to_linear_after_model(  # noqa: PLR0911, PLR0912
    state: LinearNotifyState,
    runtime: Runtime,  # noqa: ARG001
) -> dict[str, Any] | None:
    """Middleware that posts AI responses to Linear after each model call.

    Only posts if:
    - This is a Linear-triggered conversation (has linear_issue in config)
    - There's exactly 1 human message (initial request)
    - The previous message was from human (not a tool result)
    - The AI response has text content (not just tool calls)
    - The message hasn't already been sent (tracked via linear_messages_sent_count)
    """
    from ..server import comment_on_linear_issue

    try:
        config = get_config()
        configurable = config.get("configurable", {})

        linear_issue = configurable.get("linear_issue", {})
        linear_issue_id = linear_issue.get("id")

        if not linear_issue_id:
            return None

        messages = state.get("messages", [])
        if not messages:
            return None

        sent_count = state.get("linear_messages_sent_count", 0)

        human_message_count = 0
        for msg in messages:
            if isinstance(msg, dict):
                role = msg.get("role", "")
            else:
                role = getattr(msg, "type", "") or getattr(msg, "role", "")
            if role in ("human", "user"):
                human_message_count += 1

        if human_message_count != 1:
            return None

        last_message = messages[-1]
        if isinstance(last_message, dict):
            role = last_message.get("role", "")
            content = last_message.get("content", "")
        else:
            role = getattr(last_message, "type", "") or getattr(last_message, "role", "")
            content = getattr(last_message, "content", "")

        if role not in ("ai", "assistant"):
            return None

        ai_message_count = 0
        for msg in messages:
            if isinstance(msg, dict):
                r = msg.get("role", "")
            else:
                r = getattr(msg, "type", "") or getattr(msg, "role", "")
            if r in ("ai", "assistant"):
                ai_message_count += 1

        if ai_message_count <= sent_count:
            return None

        if len(messages) >= MIN_MESSAGES_FOR_PREV_CHECK:
            prev_message = messages[-2]
            if isinstance(prev_message, dict):
                prev_role = prev_message.get("role", "")
            else:
                prev_role = getattr(prev_message, "type", "") or getattr(prev_message, "role", "")

            if prev_role not in ("human", "user"):
                return None

        if not content or not isinstance(content, str):
            return None

        comment = f"""🤖 **Agent Response**

{content}"""
        logger.info("Posting AI response to Linear issue %s", linear_issue_id)
        success = await comment_on_linear_issue(linear_issue_id, comment)

        if success:
            logger.info("Successfully posted to Linear")
            return {"linear_messages_sent_count": ai_message_count}
        logger.warning("Failed to post to Linear")

    except Exception:
        logger.exception("Error in post_to_linear_after_model")
    return None
