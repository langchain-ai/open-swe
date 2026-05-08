import asyncio
from typing import Any

from langgraph.config import get_config

from ..utils.slack import set_slack_thread_status


def report_status(status: str) -> dict[str, Any]:
    """Post a short progress status to the Slack thread (shimmery text under
    the composer — NOT a visible message in the thread).

    Use this aggressively to keep the user informed while you work — the
    status appears as ``<bot-name> <status>`` (e.g. "openswe-bot is cloning
    the repo...") and shimmers until you replace it or post a real reply.
    Slack clears it automatically when you send a real message.

    Good statuses are short present-tense verb phrases, 3–8 words:
    - "is cloning langchainplus..."
    - "is reading hello.py..."
    - "is running the tests..."
    - "is drafting the PR description..."

    Call this frequently — every tool-using step should update the status so
    the user knows what you are doing without you flooding the thread with
    chat messages. Do NOT use this for final results or questions — use
    ``slack_thread_reply`` for those.

    Args:
        status: A short present-tense phrase. The bot's name is prepended
            automatically, so start your status mid-sentence ("is doing X").

    Returns:
        ``{"success": True}`` on success, ``{"success": False, "error": ...}``
        otherwise. Missing Slack context (non-Slack triggers) returns a
        no-op success so callers can invoke it unconditionally.
    """
    config = get_config()
    configurable = config.get("configurable", {})
    slack_thread = configurable.get("slack_thread") or {}

    channel_id = slack_thread.get("channel_id")
    thread_ts = slack_thread.get("thread_ts")
    if not channel_id or not thread_ts:
        return {"success": True, "skipped": "no-slack-context"}

    if not status or not status.strip():
        return {"success": False, "error": "status cannot be empty"}

    success = asyncio.run(set_slack_thread_status(channel_id, thread_ts, status.strip()))
    return {"success": success}
