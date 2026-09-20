import re
from typing import Any

from agent.slack.channels import SlackChannel
from agent.slack.client import (
    SLACK_THREAD_MAX_MESSAGES,
    SlackThreadFetchError,
    fetch_slack_thread_messages,
    format_slack_messages_for_prompt,
    get_slack_user_names,
)


async def _fetch_and_format(channel_id: str, message_ts: str) -> dict[str, Any]:
    """Fetch thread messages and resolve author names."""
    try:
        messages = await fetch_slack_thread_messages(channel_id, message_ts)
    except SlackThreadFetchError as exc:
        if exc.error_code in {"thread_not_found", "message_not_found"}:
            return {
                "success": False,
                "error": "Slack could not resolve that thread timestamp. Convert a Slack "
                "permalink timestamp to seconds plus a dot and six digits from the end "
                "(for example, 1789750092.207789), not 1789750092207789.",
            }
        return {
            "success": False,
            "error": f"Could not fetch the Slack thread ({exc.error_code}).",
        }

    user_ids = [
        user_id for msg in messages if isinstance(user_id := msg.get("user"), str) and user_id
    ]
    user_names = await get_slack_user_names(user_ids) if user_ids else {}

    truncated = len(messages) >= SLACK_THREAD_MAX_MESSAGES
    formatted = format_slack_messages_for_prompt(messages, user_names)
    if truncated:
        formatted = (
            f"[thread truncated — showing most recent {len(messages)} messages]\n{formatted}"
        )
    return {
        "success": True,
        "formatted": formatted,
        "count": len(messages),
        "truncated": truncated,
    }


async def slack_read_thread_messages(channel_id: str, message_ts: str) -> dict[str, Any]:
    """Implement the `slack_read_thread_messages` tool."""
    channel_id = channel_id.strip() if isinstance(channel_id, str) else ""
    message_ts = message_ts.strip() if isinstance(message_ts, str) else ""
    if not channel_id:
        return {"success": False, "error": "channel_id is required"}
    if not message_ts:
        return {"success": False, "error": "message_ts is required"}
    if not re.fullmatch(r"\d{10}\.\d{6}", message_ts):
        return {
            "success": False,
            "error": "message_ts must be Slack seconds plus a dot and six digits from the "
            "end of the permalink timestamp (for example, 1789750092.207789), not the "
            "16-digit permalink value.",
        }
    if await SlackChannel.load(channel_id) is None:
        return {
            "success": False,
            "error": "The bot cannot read that Slack channel. Retrying will not help; ask "
            "the sender to paste the thread or invite the bot to the channel.",
        }

    result = await _fetch_and_format(channel_id, message_ts)
    if not result.get("success"):
        return result

    return result
