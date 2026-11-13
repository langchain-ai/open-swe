"""Cross-links between a Slack thread and the breakout thread started from it."""

from openswe.slack.blocks import Block, context, plain_text, section
from openswe.slack.client import (
    SlackNotice,
    format_slack_web_link_footer,
    get_slack_permalink,
    post_slack_thread_reply,
)
from openswe.slack.links import SlackLink
from openswe.slack.request import SlackRequest
from openswe.utils.dashboard_links import dashboard_thread_url


def breakout_root_blocks(
    text: str, dashboard_url: str | None, details: str | None = None
) -> list[Block]:
    blocks: list[Block] = [section(text)]
    if details:
        blocks.append(
            {
                "type": "container",
                "title": plain_text("Instructions for the new thread"),
                "is_collapsible": True,
                "default_collapsed": True,
                "child_blocks": [section(details)],
            }
        )
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
        or SlackLink(breakout_channel_id, breakout_ts).url
    )
    where = "" if breakout_channel_id == channel_id else f" in <#{breakout_channel_id}>"
    text = f"Continued in <{permalink}|the breakout thread>{where}."
    await notify_breakout(channel_id, thread_ts, text)


async def notify_web_breakout(request: SlackRequest) -> None:
    """Tell the Slack thread a ``/breakout:web`` came from where it continues."""
    await notify_breakout(
        request.context_channel_id,
        request.context_thread_ts,
        f"<@{request.user_id}> continued in a "
        f"<{dashboard_thread_url(request.thread_id or '')}|private web breakout>.",
    )


async def notify_breakout(channel_id: str, thread_ts: str, text: str) -> None:
    """Say where a breakout continues, as a notice: the source thread's agent never
    wrote it, so its later turns read it from Slack with the command it answers."""
    await post_slack_thread_reply(
        channel_id,
        thread_ts,
        text,
        blocks=[
            {
                "type": "context",
                "block_id": SlackNotice(kind="breakout", key=text).block_id,
                "elements": [{"type": "mrkdwn", "text": text}],
            }
        ],
        unfurl_links=False,
        unfurl_media=False,
    )
