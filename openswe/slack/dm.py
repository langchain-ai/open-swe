"""Bot DMs, optionally run in concierge mode: one conversation instead of a thread per message.

Off unless the person turns on ``concierge_mode`` in their own preferences. When
it is on, the conversation is keyed with the same non-message timestamp code
channels and incident channels use, so replies post into the DM instead of
opening a thread and the channel's own history is the conversation transcript. That
timestamp is also how the rest of the code recognizes the mode: only a DM the
owner enabled ever reaches a run with it.
"""

import logging
from typing import Any, Self

from pydantic import BaseModel

from openswe.prompts import prompt
from openswe.slack.client import get_slack_permalink, post_slack_top_level_message_with_ts
from openswe.slack.http import SLACK_REQUEST_ERRORS, SlackClient, SlackRequestError, slack_error
from openswe.slack.payloads import SlackChannelContext
from openswe.slack.records import SlackMessage, lookup_slack_thread_id
from openswe.slack.thread_notes import note_for_thread_owner
from openswe.users import User
from openswe.utils.thread_ops import queue_message_for_thread

logger = logging.getLogger(__name__)

CONCIERGE_TS = "0"


def is_dm_channel(channel_context: SlackChannelContext | None) -> bool:
    """Whether Slack reports this channel as a direct message with the bot."""
    return channel_context is not None and channel_context.is_im is True


def is_concierge_thread(channel_context: SlackChannelContext | None, thread_ts: str) -> bool:
    """Whether this location is a DM running in concierge mode."""
    return is_dm_channel(channel_context) and thread_ts == CONCIERGE_TS


def dm_thread_title(name: str) -> str:
    """The fixed name of a person's DM thread, or "" until Slack tells us who they are.

    An empty title leaves the thread unnamed rather than naming it after one
    request, so the next message can still name it for the person.
    """
    return f"DMs between Open SWE and {name.strip()}" if name.strip() else ""


async def note_for_concierge(slack_user_id: str, dm_channel_id: str, note: str) -> None:
    """Queue ``note`` for the person's concierge thread, which skips the bot's own DM posts."""
    if not await User.concierge_mode_for_slack(slack_user_id):
        return
    thread_id = await lookup_slack_thread_id(dm_channel_id, CONCIERGE_TS)
    if thread_id is None:
        return
    if not await queue_message_for_thread(thread_id, [{"type": "text", "text": note}]):
        logger.warning(
            "Could not queue a note for the concierge thread",
            extra={"slack_user_id": slack_user_id, "agent_thread_id": thread_id},
        )


async def open_dm(slack_user_id: str) -> str | None:
    try:
        return await _open_dm_channel(slack_user_id)
    except SlackRequestError as exc:
        logger.warning(
            "Slack DM could not be opened",
            extra={"slack_user": slack_user_id, "slack_error": exc.code},
        )
        return None


async def _open_dm_channel(slack_user_id: str) -> str:
    try:
        async with SlackClient.bot() as client:
            response = await client.conversations_open(users=slack_user_id)
    except SLACK_REQUEST_ERRORS as exc:
        raise SlackRequestError(slack_error(exc)) from exc
    channel = response.get("channel")
    channel_id = channel.get("id") if isinstance(channel, dict) else None
    if not isinstance(channel_id, str) or not channel_id:
        raise SlackRequestError("missing_channel")
    return channel_id


class DmOrigin(BaseModel):
    """The Slack thread a DM was sent on behalf of, so a reply to the DM can find its way back."""

    channel_id: str
    thread_ts: str
    subject: str = ""

    @property
    def location(self) -> tuple[str, str]:
        return self.channel_id, self.thread_ts

    async def save_for(self, dm_channel_id: str, message_ts: str) -> None:
        try:
            await SlackMessage.save_origin(
                dm_channel_id, message_ts, self.channel_id, self.thread_ts, self.subject
            )
        except Exception:
            logger.warning(
                "Could not record where a DM came from",
                extra={"slack_channel": dm_channel_id, "slack_message_ts": message_ts},
                exc_info=True,
            )

    @classmethod
    async def of(cls, dm_channel_id: str, message_ts: str) -> Self | None:
        """The thread a bot DM was sent on behalf of; ``None`` for any other message."""
        message = await SlackMessage.origin(dm_channel_id, message_ts)
        if message is None or message.origin_channel_id is None:
            return None
        return cls(
            channel_id=message.origin_channel_id,
            thread_ts=message.origin_thread_ts or "",
            subject=message.origin_subject or "",
        )


async def _record_in_concierge_thread(
    channel_id: str, text: str, origin: DmOrigin | None = None
) -> None:
    """Add a message the bot sent to the person's concierge conversation, so a reply has context."""
    thread_id = await lookup_slack_thread_id(channel_id, CONCIERGE_TS)
    if thread_id is None:
        return
    permalink = await get_slack_permalink(*origin.location) if origin is not None else None
    note = prompt("slack/concierge-dm-posted", text=text, origin=origin, permalink=permalink or "")
    if not await queue_message_for_thread(thread_id, [{"type": "text", "text": note}]):
        logger.warning(
            "Could not queue a DM for the concierge thread",
            extra={"thread_id": thread_id},
        )


async def send_dm_with_location(
    slack_user_id: str,
    text: str,
    *,
    blocks: list[dict[str, Any]] | None = None,
    origin: DmOrigin | None = None,
) -> tuple[str, str] | None:
    """DM a person as the bot; in concierge mode the message joins their one DM conversation.

    With ``origin``, a reply to the DM can find the thread it was sent for, and that thread's
    agent learns it was sent without starting a run. ``None`` when Slack refused the DM.
    """
    try:
        return await post_dm(slack_user_id, text, blocks=blocks, origin=origin)
    except SlackRequestError as exc:
        logger.warning(
            "Slack DM could not be sent",
            extra={"slack_user": slack_user_id, "slack_error": exc.code},
        )
        return None


async def post_dm(
    slack_user_id: str,
    text: str,
    *,
    blocks: list[dict[str, Any]] | None = None,
    origin: DmOrigin | None = None,
) -> tuple[str, str]:
    """:func:`send_dm_with_location`, raising ``SlackRequestError`` with Slack's reason."""
    channel_id = await _open_dm_channel(slack_user_id)
    message_ts = await post_slack_top_level_message_with_ts(
        channel_id, text, unfurl_links=False, unfurl_media=False, blocks=blocks
    )
    if origin is not None:
        await origin.save_for(channel_id, message_ts)
        await note_for_thread_owner(
            *origin.location,
            prompt("slack/dm-sent-for-thread", recipient=f"<@{slack_user_id}>", text=text),
        )
    if await User.concierge_mode_for_slack(slack_user_id):
        await _record_in_concierge_thread(channel_id, text, origin)
    return channel_id, message_ts


async def send_dm(
    slack_user_id: str,
    text: str,
    *,
    blocks: list[dict[str, Any]] | None = None,
    origin: DmOrigin | None = None,
) -> bool:
    """Send a DM and record it in the concierge conversation and, with ``origin``, its thread."""
    sent = await send_dm_with_location(slack_user_id, text, blocks=blocks, origin=origin)
    return sent is not None
