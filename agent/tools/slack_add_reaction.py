from typing import Any

from langgraph.config import get_config

from ..utils.slack import add_slack_reaction, get_active_slack_thread
from ..utils.thread_ops import langgraph_client


async def slack_add_reaction(
    emoji: str,
    message_ts: str,
) -> dict[str, Any]:
    """Commit to acting on a Slack message by adding a context-appropriate reaction.

    Use this only when work will continue and always follow up with the outcome; never react to
    a message you are going to stay silent on. Prefer `saluting_face` for taking ownership,
    `thinking_face` for investigation, and `tada` for genuine wins. Never use
    `white_check_mark`, because teams use it to indicate that a pull request is approved.
    To target a specific message, pass its `message_ts` identifier shown in Slack context.
    Pass emoji names without surrounding colons.
    """
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

    target_ts = message_ts.strip()
    if not target_ts:
        return {"success": False, "error": "message_ts is required"}

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

    error = await add_slack_reaction(channel_id, target_ts, reaction)
    if error is not None:
        return {
            "success": False,
            "error": f"Slack reactions.add failed: {error}",
            "channel_id": channel_id,
            "target_ts": target_ts,
        }
    return {"success": True}
