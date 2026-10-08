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

from pydantic import BaseModel, ValidationError

from openswe.human_review.notices import ReviewNotice
from openswe.prompts import prompt
from openswe.slack.client import (
    get_slack_permalink,
    lookup_slack_thread_id,
    post_slack_top_level_message_with_ts,
)
from openswe.slack.http import SLACK_REQUEST_ERRORS, SlackClient, SlackRequestError, slack_error
from openswe.slack.payloads import SlackChannelContext
from openswe.slack.thread_notes import note_for_thread_owner
from openswe.users import User
from openswe.utils.thread_ops import langgraph_client, queue_message_for_thread

logger = logging.getLogger(__name__)

CONCIERGE_TS = "0"
_DM_ORIGIN_NAMESPACE = "slack_dm_origin"


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
    thread_id = await lookup_slack_thread_id(langgraph_client(), dm_channel_id, CONCIERGE_TS)
    if thread_id is None:
        return
    if not await queue_message_for_thread(thread_id, [{"type": "text", "text": note}]):
        logger.warning(
            "Could not queue a note for the concierge thread",
            extra={"slack_user_id": slack_user_id, "agent_thread_id": thread_id},
        )


async def open_dm(slack_user_id: str) -> str | None:
    try:
        async with SlackClient.bot() as client:
            response = await client.conversations_open(users=slack_user_id)
    except SLACK_REQUEST_ERRORS as exc:
        logger.warning(
            "Slack DM could not be opened",
            extra={"slack_user": slack_user_id, "slack_error": slack_error(exc)},
        )
        return None
    channel = response.get("channel")
    channel_id = channel.get("id") if isinstance(channel, dict) else None
    return channel_id if isinstance(channel_id, str) and channel_id else None


class DmOrigin(BaseModel):
    """The Slack thread a DM was sent on behalf of, so a reply to the DM can find its way back."""

    channel_id: str
    thread_ts: str
    subject: str = ""
    notice: ReviewNotice | None = None

    @property
    def location(self) -> tuple[str, str]:
        return self.channel_id, self.thread_ts

    async def save_for(self, dm_channel_id: str, message_ts: str, text: str) -> None:
        saved = self
        if self.notice is not None and not self.notice.text:
            saved = self.model_copy(
                update={"notice": self.notice.model_copy(update={"text": text})}
            )
        try:
            await langgraph_client().store.put_item(
                (_DM_ORIGIN_NAMESPACE, dm_channel_id), message_ts, saved.model_dump()
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
        item = await langgraph_client().store.get_item(
            (_DM_ORIGIN_NAMESPACE, dm_channel_id), message_ts
        )
        if item is None:
            return None
        try:
            return cls.model_validate(item["value"])
        except ValidationError:
            logger.warning(
                "Ignoring an unreadable DM origin",
                extra={"slack_channel": dm_channel_id, "slack_message_ts": message_ts},
                exc_info=True,
            )
            return None


async def _record_in_concierge_thread(
    channel_id: str, text: str, origin: DmOrigin | None = None
) -> None:
    """Add a message the bot sent to the person's concierge conversation, so a reply has context."""
    thread_id = await lookup_slack_thread_id(langgraph_client(), channel_id, CONCIERGE_TS)
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
    agent learns it was sent without starting a run.
    """
    channel_id = await open_dm(slack_user_id)
    if channel_id is None:
        return None
    try:
        message_ts = await post_slack_top_level_message_with_ts(
            channel_id, text, unfurl_links=False, unfurl_media=False, blocks=blocks
        )
    except SlackRequestError as exc:
        logger.warning(
            "Slack DM could not be posted",
            extra={"slack_user": slack_user_id, "slack_error": exc.code},
        )
        return None
    if origin is not None:
        await origin.save_for(channel_id, message_ts, text)
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
