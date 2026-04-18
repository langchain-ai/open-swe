import asyncio
from typing import Any

from langgraph.config import get_config

from ..utils.options import slackv2_enabled
from ..utils.slack import (
    convert_mentions_to_slack_format,
    post_slack_message,
    post_slack_thread_reply,
)


def slack_thread_reply(message: str) -> dict[str, Any]:
    """Post a message to the current Slack thread.

    Follow-up messages from the same user in this thread will be routed to you
    automatically — they do not need to re-@mention you. Keep the conversation
    natural and reply inline in the thread.

    Response format: Slack mrkdwn (not standard Markdown).
    - Use Slack mrkdwn, not standard Markdown.
    - Bold uses *single asterisks*.
    - Italic uses _underscores_, strikethrough uses ~tildes~.
    - Links use <url|label>.
    - Code blocks use triple backticks without a language identifier.
    - Do not use markdown headings or pipe tables.

    To tag a user, use Slack's mention format: <@USER_ID>. You can find user
    IDs in the conversation context next to display names (e.g.
    @Name(U06KD8BFY95)). Example: <@U06KD8BFY95> tags that user."""
    config = get_config()
    configurable = config.get("configurable", {})
    slack_thread = configurable.get("slack_thread", {})

    channel_id = slack_thread.get("channel_id")
    thread_ts = slack_thread.get("thread_ts")
    channel_type = slack_thread.get("channel_type") or ""
    if not channel_id or not thread_ts:
        return {
            "success": False,
            "error": "Missing slack_thread.channel_id or slack_thread.thread_ts in config",
        }

    if not message.strip():
        return {"success": False, "error": "Message cannot be empty"}

    message = convert_mentions_to_slack_format(message)
    # In slackv2 mode, DMs reply as top-level messages (no thread_ts) — a
    # DM is already a 1:1 conversation, threading makes it feel stilted.
    if slackv2_enabled() and channel_type == "im":
        success = asyncio.run(post_slack_message(channel_id, message))
    else:
        success = asyncio.run(post_slack_thread_reply(channel_id, thread_ts, message))
    return {"success": success}
