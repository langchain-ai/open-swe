"""Cross-links between a Slack thread and the breakout thread started from it."""

from agent.slack.client import add_slack_reaction, get_slack_permalink

BREAKOUT_REACTION = "leftwards_arrow_with_hook"


async def source_thread_line(channel_id: str, message_ts: str) -> str:
    permalink = await get_slack_permalink(channel_id, message_ts)
    if not permalink:
        return ""
    return f"<{permalink}|from this thread>"


async def mark_broken_out(channel_id: str, request_ts: str) -> None:
    await add_slack_reaction(channel_id, request_ts, BREAKOUT_REACTION)
