import asyncio
import os
from typing import Any

from langgraph.config import get_config
from langgraph_sdk import get_client

from ..utils.slack import (
    convert_mentions_to_slack_format,
    post_slack_thread_reply_with_ts,
    store_slack_message_run_mapping,
)

LANGGRAPH_URL = os.environ.get("LANGGRAPH_URL") or os.environ.get(
    "LANGGRAPH_URL_PROD", "http://localhost:2024"
)


def slack_thread_reply(message: str) -> dict[str, Any]:
    """Post a message to the current Slack thread.

    Use this for clarifying questions, mid-run progress updates, and the final
    summary. You can call this multiple times during a run — if you're about to
    do long-running work (cloning, large refactors, big test runs) consider
    posting a brief status update first so the user knows what's happening.
    Always end the run with a final reply summarizing what you did.

    Format messages using Slack's mrkdwn format, NOT standard Markdown.
    Key differences: *bold*, _italic_, ~strikethrough~, <url|link text>,
    bullet lists with "• ", ```code blocks```, > blockquotes.
    Do NOT use **bold**, [link](url), or other standard Markdown syntax.

    To mention/tag a user, use Slack's mention format: <@USER_ID>.
    You can find user IDs in the conversation context (e.g. @Name(U06KD8BFY95)).
    Example: <@U06KD8BFY95> will tag that user in the message."""
    config = get_config()
    configurable = config.get("configurable", {})
    slack_thread = configurable.get("slack_thread", {})

    channel_id = slack_thread.get("channel_id")
    thread_ts = slack_thread.get("thread_ts")
    if not channel_id or not thread_ts:
        return {
            "success": False,
            "error": "Missing slack_thread.channel_id or slack_thread.thread_ts in config",
        }

    if not message.strip():
        return {"success": False, "error": "Message cannot be empty"}

    message = convert_mentions_to_slack_format(message)
    message_ts, slack_error = asyncio.run(
        _post_and_store_mapping(channel_id, thread_ts, message)
    )
    if message_ts:
        return {"success": True}
    return {
        "success": False,
        "error": slack_error or "post failed",
        "slack_error": slack_error,
        "message_chars": len(message),
        "hint": (
            "If slack_error is 'msg_too_long', retry with a shorter message "
            "(Slack's hard limit is ~40K chars but practical limits are much "
            "lower in some workspaces — try splitting into multiple replies). "
            "If slack_error is 'channel_not_found' or 'not_in_channel', do not "
            "retry — the bot cannot post here; surface the failure in the "
            "trace output so the user knows the response was not delivered. "
            "If slack_error starts with 'http_error:' or is 'rate_limited', "
            "a single retry may succeed. Never emit a final response as if "
            "the user received it when this tool returns success: False."
        ),
    }


async def _post_and_store_mapping(
    channel_id: str, thread_ts: str, message: str
) -> tuple[str | None, str | None]:
    message_ts, slack_error = await post_slack_thread_reply_with_ts(
        channel_id, thread_ts, message
    )
    if message_ts:
        langgraph_client = get_client(url=LANGGRAPH_URL)
        await store_slack_message_run_mapping(langgraph_client, channel_id, thread_ts, message_ts)
    return message_ts, slack_error
