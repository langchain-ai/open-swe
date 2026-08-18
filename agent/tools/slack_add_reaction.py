from typing import Any

from langgraph.config import get_config
from langgraph_sdk import get_client

from ..utils.slack import LANGGRAPH_URL, add_slack_reaction, get_active_slack_thread


async def slack_add_reaction(
    emoji: str,
    message_ts: str | None = None,
) -> dict[str, Any]:
    """Add a context-appropriate reaction to a Slack message in the current thread.

    Prefer `saluting_face` for taking ownership, `eyes` for active review,
    `thinking_face` for investigation, and `tada` for genuine wins. Never use
    `white_check_mark`, because teams use it to indicate that a pull request is approved.
    If `message_ts` is omitted, this reacts to the latest message that triggered the run.
    Pass emoji names without surrounding colons.
    """
    config = get_config()
    configurable = config.get("configurable", {})
    slack_thread = configurable.get("slack_thread", {})
    thread_id = configurable.get("thread_id")
    active = await get_active_slack_thread(
        get_client(url=LANGGRAPH_URL),
        thread_id if isinstance(thread_id, str) else None,
        slack_thread if isinstance(slack_thread, dict) else None,
    )
    active = active or {}

    channel_id = active.get("channel_id")
    if not channel_id:
        return {"success": False, "error": "Missing slack_thread.channel_id in config"}

    configured = slack_thread if isinstance(slack_thread, dict) else {}
    same_location = (active.get("channel_id"), active.get("thread_ts")) == (
        configured.get("channel_id"),
        configured.get("thread_ts"),
    )
    default_target_ts = (
        configured.get("triggering_event_ts")
        if same_location
        else active.get("triggering_event_ts")
    )
    target_ts = (message_ts or default_target_ts or "").strip()
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
