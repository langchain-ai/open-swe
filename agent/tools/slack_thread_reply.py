import asyncio
from typing import Any

from langgraph.config import get_config

from ..utils.slack import convert_mentions_to_slack_format, post_slack_thread_reply


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
    success = asyncio.run(post_slack_thread_reply(channel_id, thread_ts, message))
    return {"success": success}
