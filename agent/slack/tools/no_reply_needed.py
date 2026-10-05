import logging
from typing import Any

from langgraph.config import get_config

from agent.tools.errors import ToolError

logger = logging.getLogger(__name__)

REQUIRED_CONFIRMATION = "The user cannot see anything I do not send to Slack."


def _normalized(text: str) -> str:
    return " ".join(text.split()).rstrip(".").casefold()


async def slack_no_reply_needed(reason: str, confirmation: str) -> dict[str, Any]:
    """Implement the `slack_no_reply_needed` tool."""
    stated = reason.strip()
    if not stated:
        raise ToolError(
            "reason is required",
            details={"hint": "State in one sentence why this turn warrants no reply."},
        )
    if _normalized(confirmation) != _normalized(REQUIRED_CONFIRMATION):
        raise ToolError(
            "confirmation does not match",
            details={
                "hint": f"Type exactly: {REQUIRED_CONFIRMATION} If the asker is waiting on anything, send it with `slack_reply` instead."
            },
        )
    configurable = get_config().get("configurable", {})
    logger.info(
        "Turn declared as needing no user-facing reply",
        extra={
            "agent_thread_id": configurable.get("thread_id"),
            "no_reply_reason": stated,
        },
    )
    return {"success": True}
