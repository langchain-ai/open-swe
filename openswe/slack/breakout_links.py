"""Cross-links between a Slack thread and the breakout thread started from it."""

from openswe.slack.blocks import Block, context, section
from openswe.slack.client import (
    format_slack_web_link_footer,
    get_slack_permalink,
    post_slack_thread_reply,
)


def breakout_root_blocks(text: str, dashboard_url: str | None) -> list[Block]:
    blocks: list[Block] = [section(text)]
    footer = format_slack_web_link_footer(dashboard_url)
    if footer:
        blocks.append(context(footer))
    return blocks


async def source_thread_line(channel_id: str, message_ts: str) -> str:
    permalink = await get_slack_permalink(channel_id, message_ts)
    if not permalink:
        return ""
    return f"<{permalink}|(source)>"


async def mark_broken_out(
    channel_id: str,
    thread_ts: str,
    breakout_channel_id: str,
    breakout_ts: str,
) -> None:
    """Link the breakout thread from the source thread."""
    permalink = (
        await get_slack_permalink(breakout_channel_id, breakout_ts)
        or f"https://slack.com/archives/{breakout_channel_id}/p{breakout_ts.replace('.', '')}"
    )
    where = "" if breakout_channel_id == channel_id else f" in <#{breakout_channel_id}>"
    text = f"Continued in <{permalink}|the breakout thread>{where}."
    await notify_breakout(channel_id, thread_ts, text)


async def notify_breakout(channel_id: str, thread_ts: str, text: str) -> None:
    await post_slack_thread_reply(
        channel_id,
        thread_ts,
        text,
        blocks=[{"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}],
        unfurl_links=False,
        unfurl_media=False,
    )
