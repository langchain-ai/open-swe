"""Cross-links between a Slack thread and the breakout thread started from it."""

from agent.slack.client import get_slack_permalink, post_slack_thread_reply


async def source_thread_line(channel_id: str, source_ts: str) -> str:
    permalink = await get_slack_permalink(channel_id, source_ts)
    if not permalink:
        return ""
    return f":arrow_right_hook: Broken out from <{permalink}|this thread>"


async def post_breakout_link(channel_id: str, source_ts: str, breakout_ts: str) -> None:
    permalink = await get_slack_permalink(channel_id, breakout_ts)
    link = (
        f"<{permalink}|Continued in a breakout thread>"
        if permalink
        else "Continued in a breakout thread"
    )
    await post_slack_thread_reply(channel_id, source_ts, f":leftward_arrow_with_hook: {link}")
