import logging
from typing import Any

from langgraph.config import get_config

from agent.run_config import RunConfig
from agent.slack.client import (
    SLACK_CHANNEL_HISTORY_MAX_MESSAGES,
    fetch_slack_channel_messages,
    format_slack_messages_for_prompt,
    get_slack_user_names,
    slack_channel_is_public,
)
from agent.threads.summary import thread_is_private
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_client as get_langgraph_client

logger = logging.getLogger(__name__)

DEFAULT_CHANNEL_MESSAGE_LIMIT = 30

_NOT_PUBLIC = (
    "Only public channels can be read this way. That channel is private, a direct message, "
    "or shared with another organization, and reading it here would bypass its membership. "
    "Ask the person for what you need instead."
)
_NOT_PRIVATE_THREAD = (
    "Channel history can only be read into a private thread, because everyone who can see "
    "this thread would see what it pulls in. Ask the person to paste what you need."
)


async def _thread_is_private() -> bool:
    """Whether this run's thread is one only its owner can read. Fails closed."""
    thread_id = RunConfig.from_config(get_config()).thread_id
    if not thread_id:
        return False
    try:
        thread = await get_langgraph_client().threads.get(str(thread_id))
    except Exception:
        logger.exception("Could not read thread visibility", extra={"agent_thread_id": thread_id})
        return False
    return thread_is_private(thread_metadata(thread))


async def slack_read_channel_messages(channel_id: str, limit: int = 30) -> dict[str, Any]:
    """Implement the `slack_read_channel_messages` tool."""
    if not channel_id or not channel_id.strip():
        return {"success": False, "error": "channel_id is required"}
    if not await _thread_is_private():
        return {"success": False, "error": _NOT_PRIVATE_THREAD}
    if not await slack_channel_is_public(channel_id.strip()):
        return {"success": False, "error": _NOT_PUBLIC}

    requested = limit if isinstance(limit, int) and limit > 0 else DEFAULT_CHANNEL_MESSAGE_LIMIT
    messages = await fetch_slack_channel_messages(
        channel_id.strip(), min(requested, SLACK_CHANNEL_HISTORY_MAX_MESSAGES)
    )
    if not messages:
        return {
            "success": False,
            "error": "Could not read that channel. The bot may not be a member, or the "
            "channel may have no messages.",
        }

    user_ids = [
        user_id for msg in messages if isinstance(user_id := msg.get("user"), str) and user_id
    ]
    user_names = await get_slack_user_names(user_ids) if user_ids else {}
    return {
        "success": True,
        "formatted": format_slack_messages_for_prompt(
            messages, user_names, include_thread_replies=True
        ),
        "count": len(messages),
    }
