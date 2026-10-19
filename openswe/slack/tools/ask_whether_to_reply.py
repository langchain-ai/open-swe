from typing import Any

from langgraph.config import get_config

from openswe.slack.client import (
    fetch_slack_thread_message_by_ts,
    get_active_slack_thread,
    slack_message_bot_id,
)
from openswe.slack.suggested_actions import REPLY
from openswe.utils.thread_ops import langgraph_client


async def slack_ask_whether_to_reply(message_ts: str) -> dict[str, Any]:
    """Implement the `slack_ask_whether_to_reply` tool."""
    configurable = get_config().get("configurable", {})
    slack_thread = configurable.get("slack_thread", {})
    thread_id = configurable.get("thread_id")
    active = await get_active_slack_thread(
        langgraph_client(),
        thread_id if isinstance(thread_id, str) else None,
        slack_thread if isinstance(slack_thread, dict) else None,
    )
    channel_id = str((active or {}).get("channel_id") or "")
    thread_ts = str((active or {}).get("thread_ts") or "")
    if not (channel_id and thread_ts):
        return {"success": False, "error": "This run has no Slack thread"}

    target_ts = message_ts.strip()
    message = await fetch_slack_thread_message_by_ts(channel_id, thread_ts, target_ts)
    sender = message.get("user") if message else None
    if message is None or slack_message_bot_id(message) or not isinstance(sender, str):
        return {
            "success": False,
            "error": "No message from a person at that message_ts in this Slack thread",
        }

    if not await REPLY.suggest(
        channel_id, sender, target_ts, thread_ts=thread_ts, reply_thread_ts=thread_ts
    ):
        return {"success": False, "error": "Could not send the question to the sender"}
    return {
        "success": True,
        "next": (
            "Only the sender sees the question. Post nothing in the thread: end the turn "
            "with slack_no_reply_needed. If they click Reply, you get a new turn to answer."
        ),
    }
