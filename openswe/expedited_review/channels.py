"""The channels an expedited card offers to be sent to: its own, then its author's recent ones.

Only public channels that are not externally shared are offered, since every option's
name is shown to everyone who can see the card.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from openswe.human_review.requests import ChannelChoice, HumanReviewRequest
from openswe.slack.channels import SlackChannel
from openswe.slack.code_channels import is_code_channel_session
from openswe.source_context import SourceContext
from openswe.threads.summary import thread_is_private, thread_updated_ms
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_ops import langgraph_client
from openswe.utils.thread_participants import participant_search_filters

logger = logging.getLogger(__name__)

MAX_CHOICES = 10
THREAD_WINDOW = timedelta(days=14)
# A channel picked with "Other" is remembered longer than one a thread happened to run in.
COPY_WINDOW = timedelta(days=90)
_THREAD_SCAN = 50
_SEARCH_TIMEOUT_SECONDS = 2.0


async def _thread_channels(login: str, since: datetime) -> list[str]:
    """Slack channels of ``login``'s recent threads, newest first."""
    try:
        async with asyncio.timeout(_SEARCH_TIMEOUT_SECONDS):
            threads = await langgraph_client().threads.search(
                metadata=participant_search_filters(login)[0],
                limit=_THREAD_SCAN,
                sort_by="updated_at",
                sort_order="desc",
                select=["thread_id", "metadata", "updated_at"],
            )
    except Exception:
        logger.warning(
            "Could not read the author's recent threads for channel choices",
            extra={"github_login": login},
            exc_info=True,
        )
        return []
    cutoff_ms = since.timestamp() * 1000
    channels: list[str] = []
    for thread in threads:
        if thread_updated_ms(thread) < cutoff_ms:
            break
        metadata = thread_metadata(thread)
        if thread_is_private(metadata):
            continue
        slack_thread = SourceContext.from_metadata(metadata).slack_thread
        if slack_thread is None or is_code_channel_session(slack_thread.thread_ts):
            continue
        channels.append(slack_thread.channel_id)
    return channels


def _can_receive(channel: SlackChannel | None) -> bool:
    return channel is not None and channel.public and channel.context.allows_operations


async def _offerable(channel_id: str) -> ChannelChoice | None:
    channel = await SlackChannel.load(channel_id)
    if channel is None or not channel.name or not _can_receive(channel):
        return None
    return {"id": channel.id, "name": channel.name}


async def own_choices(approval: HumanReviewRequest) -> list[ChannelChoice]:
    """Just the card's own channel, which a thread reply can be broadcast to; empty outside one."""
    if not approval.slack_thread_ts or is_code_channel_session(approval.slack_thread_ts):
        return []
    own = await SlackChannel.load(approval.slack_channel_id)
    if own is None or not own.name:
        return []
    return [{"id": own.id, "name": own.name}]


async def channel_choices(approval: HumanReviewRequest) -> list[ChannelChoice]:
    """The card's own channel first, then up to ``MAX_CHOICES`` in all; empty outside a thread."""
    choices = await own_choices(approval)
    if not choices:
        return []
    login = approval.pull_request.author
    if not login:
        return choices
    now = datetime.now(UTC)
    from_threads, from_copies = await asyncio.gather(
        _thread_channels(login, now - THREAD_WINDOW),
        HumanReviewRequest.copy_channels_for_author(login, since=now - COPY_WINDOW),
    )
    seen = {choices[0]["id"]}
    for channel_id in [*from_copies, *from_threads]:
        if len(choices) >= MAX_CHOICES:
            break
        if not channel_id or channel_id in seen:
            continue
        seen.add(channel_id)
        if (choice := await _offerable(channel_id)) is not None:
            choices.append(choice)
    return choices


async def sendable_channel(channel_id: str) -> SlackChannel | None:
    """A channel the card may be copied into: public, and not externally shared right now."""
    channel = await SlackChannel.load(channel_id, use_cache=False)
    return channel if _can_receive(channel) else None


async def still_internal(channel_id: str) -> bool:
    """Whether Slack confirms, uncached, that the channel is not externally shared."""
    channel = await SlackChannel.load(channel_id, use_cache=False)
    return channel is not None and channel.context.allows_operations
