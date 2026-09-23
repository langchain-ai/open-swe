"""Cross-links between a Slack thread and the breakout thread started from it."""

from agent.slack.client import get_slack_permalink, post_slack_thread_reply


async def source_thread_line(channel_id: str, source_ts: str) -> str:
    permalink = await get_slack_permalink(channel_id, source_ts)
    if not permalink:
        return ""
    return f"<{permalink}|from this thread>"


async def post_breakout_link(channel_id: str, source_ts: str, breakout_ts: str) -> None:
    permalink = (
        await get_slack_permalink(channel_id, breakout_ts)
        or f"https://slack.com/archives/{channel_id}/p{breakout_ts.replace('.', '')}"
    )
    await post_slack_thread_reply(
        channel_id, source_ts, f"<{permalink}|:leftwards_arrow_with_hook:>"
    )
