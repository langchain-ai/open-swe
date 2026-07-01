from typing import Any

from langgraph.config import get_config

from ..utils.slack import add_slack_reaction


async def slack_add_reaction(
    emoji: str = "eyes",
    message_ts: str | None = None,
) -> dict[str, Any]:
    """Add a reaction to a Slack message in the current Slack thread.

    Use this with the default `eyes` reaction to acknowledge Slack user follow-up
    requests while you continue working, instead of posting a perfunctory
    confirmation reply. If `message_ts` is omitted, this reacts to the latest
    message that triggered the run. Pass emoji names without surrounding colons.
    """
    config = get_config()
    configurable = config.get("configurable", {})
    slack_thread = configurable.get("slack_thread", {})

    channel_id = slack_thread.get("channel_id")
    if not channel_id:
        return {"success": False, "error": "Missing slack_thread.channel_id in config"}

    target_ts = (message_ts or slack_thread.get("triggering_event_ts") or "").strip()
    if not target_ts:
        return {
            "success": False,
            "error": "Missing message_ts and slack_thread.triggering_event_ts in config",
        }

    reaction = emoji.strip().strip(":")
    if not reaction:
        return {"success": False, "error": "emoji is required"}
    if any(char.isspace() for char in reaction):
        return {
            "success": False,
            "error": "emoji must be a Slack reaction name without whitespace",
        }

    success = await add_slack_reaction(channel_id, target_ts, reaction)
    if not success:
        return {"success": False, "error": "Could not add Slack reaction"}
    return {"success": True}
