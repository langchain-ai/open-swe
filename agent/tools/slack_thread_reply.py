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
    message_ts, slack_error = asyncio.run(_post_and_store_mapping(channel_id, thread_ts, message))
    if message_ts is None:
        return {
            "success": False,
            "error": slack_error or "post failed",
            "slack_error": slack_error,
            "message_chars": len(message),
            "hint": _slack_reply_failure_hint(slack_error),
        }
    return {"success": True}


def _slack_reply_failure_hint(slack_error: str | None) -> str:
    if slack_error == "msg_too_long":
        return "Slack rejected the message as too long; retry with a shorter message."
    if slack_error in {"channel_not_found", "not_in_channel"}:
        return "Slack rejected the channel; do not retry. Surface the failure to the user via the trace output instead."
    if slack_error and slack_error.startswith("rate_limited"):
        retry_after = slack_error.partition(":")[2].strip()
        if retry_after:
            return f"Slack rate limited the request; wait at least {retry_after}s before retrying, or surface the failure to the user via the trace output."
        return "Slack rate limited the request; wait before retrying, or surface the failure to the user via the trace output."
    if slack_error == "missing_slack_bot_token":
        return "Slack bot token is missing; do not retry. Surface the failure to the user via the trace output instead."
    if slack_error and slack_error.startswith("http_error:"):
        return "Slack posting hit an HTTP error; retry once, then surface the failure to the user via the trace output."
    return "Slack post failed; retry once with a concise message or surface the failure to the user via the trace output."


async def _post_and_store_mapping(
    channel_id: str, thread_ts: str, message: str
) -> tuple[str | None, str | None]:
    message_ts, slack_error = await post_slack_thread_reply_with_ts(channel_id, thread_ts, message)
    if message_ts:
        langgraph_client = get_client(url=LANGGRAPH_URL)
        await store_slack_message_run_mapping(langgraph_client, channel_id, thread_ts, message_ts)
    return message_ts, slack_error
