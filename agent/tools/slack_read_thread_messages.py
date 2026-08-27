from typing import Any

from ..utils.slack import (
    fetch_and_format_slack_thread,
    get_slack_thread_version,
)
from ..utils.thread_ops import langgraph_client


async def slack_read_thread_messages(channel_id: str, message_ts: str) -> dict[str, Any]:
    """Read messages from a Slack thread.

    Use this tool to read messages from a Slack channel or thread.
    Provide the channel_id and message_ts (thread timestamp) to fetch all
    messages in that thread.

    If you encounter a Slack message URL like
    https://workspace.slack.com/archives/C0AME1J0/p1776281321762829
    you can extract the channel_id (C0AME1J0) and convert the timestamp
    by inserting a dot 6 digits from the end (1776281321.762829).

    Returns formatted thread messages with author names, forwarded-message context,
    and the current `thread_version` required by `slack_thread_reply`."""
    if not channel_id or not channel_id.strip():
        return {"success": False, "error": "channel_id is required"}
    if not message_ts or not message_ts.strip():
        return {"success": False, "error": "message_ts is required"}

    clean_channel_id = channel_id.strip()
    clean_message_ts = message_ts.strip()
    thread_version = await get_slack_thread_version(
        langgraph_client(), clean_channel_id, clean_message_ts
    )
    transcript = await fetch_and_format_slack_thread(clean_channel_id, clean_message_ts)
    if transcript is None:
        return {
            "success": False,
            "error": "Could not fetch thread messages. The bot may not have access to "
            "that channel, or the message may have been deleted.",
        }

    return {
        "success": True,
        "formatted": transcript.formatted,
        "count": transcript.count,
        "truncated": transcript.truncated,
        "thread_version": thread_version,
    }
