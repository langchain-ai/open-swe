import logging
from typing import Any

from langgraph.config import get_config

logger = logging.getLogger(__name__)


async def slack_no_reply_needed(reason: str) -> dict[str, Any]:
    """Implement the `slack_no_reply_needed` tool."""
    stated = reason.strip()
    if not stated:
        return {
            "success": False,
            "error": "reason is required",
            "hint": "State in one sentence why this turn warrants no reply.",
        }
    configurable = get_config().get("configurable", {})
    logger.info(
        "Turn declared as needing no user-facing reply",
        extra={
            "agent_thread_id": configurable.get("thread_id"),
            "no_reply_reason": stated,
        },
    )
    return {"success": True}
