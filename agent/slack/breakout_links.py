"""Cross-links between a Slack thread and the breakout thread started from it."""

from agent.slack.client import add_slack_reaction, get_slack_permalink, post_slack_thread_reply

BREAKOUT_REACTION = "leftwards_arrow_with_hook"


async def source_thread_line(channel_id: str, message_ts: str) -> str:
    permalink = await get_slack_permalink(channel_id, message_ts)
    if not permalink:
        return ""
    return f"<{permalink}|from this thread>"


async def mark_broken_out(
    channel_id: str,
    thread_ts: str,
    request_ts: str,
    breakout_channel_id: str,
    breakout_ts: str,
) -> None:
    """React to the request and link the breakout thread from the source thread."""
    await add_slack_reaction(channel_id, request_ts, BREAKOUT_REACTION)
    permalink = (
        await get_slack_permalink(breakout_channel_id, breakout_ts)
        or f"https://slack.com/archives/{breakout_channel_id}/p{breakout_ts.replace('.', '')}"
    )
    where = "" if breakout_channel_id == channel_id else f" in <#{breakout_channel_id}>"
    await post_slack_thread_reply(
        channel_id, thread_ts, f"Continued in <{permalink}|the breakout thread>{where}."
    )
