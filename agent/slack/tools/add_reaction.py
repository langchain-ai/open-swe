from typing import Any

from langgraph.config import get_config

from agent.slack.client import add_slack_reaction, get_active_slack_thread
from agent.utils.thread_ops import langgraph_client


async def slack_add_reaction(
    emoji: str,
    message_ts: str | None = None,
) -> dict[str, Any]:
    """Implement the `slack_add_reaction` tool."""
    config = get_config()
    configurable = config.get("configurable", {})
    slack_thread = configurable.get("slack_thread", {})
    thread_id = configurable.get("thread_id")
    active = await get_active_slack_thread(
        langgraph_client(),
        thread_id if isinstance(thread_id, str) else None,
        slack_thread if isinstance(slack_thread, dict) else None,
    )
    active = active or {}

    channel_id = active.get("channel_id")
    if not channel_id:
        return {"success": False, "error": "Missing slack_thread.channel_id in config"}

    target_ts = (message_ts or active.get("triggering_event_ts") or "").strip()
    if not target_ts:
        return {
            "success": False,
            "error": "Missing message_ts and slack_thread.triggering_event_ts in config",
        }

    reaction = emoji.strip().strip(":")
    if not reaction:
        return {"success": False, "error": "emoji is required"}
    if reaction == "white_check_mark":
        return {
            "success": False,
            "error": "white_check_mark is not allowed because it can imply PR approval",
        }
    if any(char.isspace() for char in reaction):
        return {
            "success": False,
            "error": "emoji must be a Slack reaction name without whitespace",
        }

    success = await add_slack_reaction(channel_id, target_ts, reaction)
    if not success:
        return {"success": False, "error": "Could not add Slack reaction"}
    return {"success": True}
