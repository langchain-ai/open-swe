"""Cross-links between a Slack thread and the breakout thread started from it."""

import logging
from typing import Self

from pydantic import BaseModel

from openswe.database import postgres
from openswe.prompts import prompt
from openswe.slack.blocks import Block, context, section
from openswe.slack.channels import SlackChannel
from openswe.slack.client import (
    format_slack_web_link_footer,
    get_slack_permalink,
    post_slack_thread_reply,
)
from openswe.slack.records import SlackMessage
from openswe.slack.request import SlackRequest

logger = logging.getLogger(__name__)


class BreakoutOrigin(BaseModel):
    """The Slack thread a breakout root was posted for.

    A breakout's first run normally starts with the root and reads this thread.
    When it can't, as when the person it would act for has no Open SWE account,
    the first run someone later starts under the root reads it instead.
    """

    channel_id: str
    thread_ts: str

    async def save_for(self, channel_id: str, root_ts: str) -> None:
        try:
            await SlackMessage.save_origin(channel_id, root_ts, self.channel_id, self.thread_ts, "")
        except Exception:
            logger.warning(
                "Could not record where a breakout came from",
                extra={"slack_channel_id": channel_id, "slack_message_ts": root_ts},
                exc_info=True,
            )

    @classmethod
    async def of(cls, channel_id: str, root_ts: str) -> Self | None:
        """The thread the root at ``root_ts`` was posted for; ``None`` for any other message.

        Fails soft: a reply that can't learn its origin still runs, with its own thread.
        """
        if not postgres.configured():
            return None
        try:
            message = await SlackMessage.origin(channel_id, root_ts)
        except Exception:
            logger.warning(
                "Could not read where a breakout came from",
                extra={"slack_channel_id": channel_id, "slack_message_ts": root_ts},
                exc_info=True,
            )
            return None
        if message is None or not message.origin_channel_id or not message.origin_thread_ts:
            return None
        return cls(channel_id=message.origin_channel_id, thread_ts=message.origin_thread_ts)

    async def readable_in(self, channel_id: str) -> bool:
        """Whether a run in ``channel_id`` may read this thread: only a public one leaves its channel."""
        if channel_id == self.channel_id:
            return True
        channel = await SlackChannel.load(self.channel_id)
        return channel is not None and channel.public

    def opening(self, request: SlackRequest) -> SlackRequest:
        """``request`` as the breakout's first run: reading this thread, told where it is."""
        note = prompt("slack/breakout-origin", channel_id=self.channel_id, thread_ts=self.thread_ts)
        return request.model_copy(
            update={
                "context_channel_id": self.channel_id,
                "context_thread_ts": self.thread_ts,
                "turn_context": "\n\n".join(part for part in (request.turn_context, note) if part),
            }
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
