"""The Slack channels the dashboard offers, read from the stored channel directory.

A job walks Slack into :mod:`openswe.slack.channels` on one replica every few
minutes, and Slack's channel events keep it current between walks, so reading
the directory never waits on Slack. Refreshing brings the next walk forward.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Final, Self
from uuid import UUID

from pydantic import BaseModel

from openswe.config import ENV
from openswe.jobs import queue
from openswe.slack.channels import SlackChannel
from openswe.ui_invalidations.topics import Topic

logger = logging.getLogger(__name__)

DIRECTORY_JOB: Final = "slack_channel_directory"
_WALK_SCHEDULE: Final = "*/15 * * * *"


class SlackChannelOption(BaseModel):
    id: str
    name: str
    is_private: bool
    is_member: bool
    is_ext_shared: bool
    is_pending_ext_shared: bool = False
    num_members: int | None = None

    @classmethod
    def of(cls, channel: SlackChannel) -> Self:
        details = channel.details
        members = channel.payload.get("num_members")
        return cls(
            id=channel.id,
            name=details.name or channel.name,
            is_private=details.is_private is True,
            is_member=details.is_member is True,
            is_ext_shared=details.is_ext_shared is True,
            is_pending_ext_shared=details.is_pending_ext_shared is True,
            num_members=members
            if isinstance(members, int) and not isinstance(members, bool)
            else None,
        )


class SlackChannelDirectory(BaseModel):
    channels: list[SlackChannelOption]
    # True while a walk over Slack is due or running; the list may change when it ends.
    refreshing: bool = False


async def list_slack_channels() -> SlackChannelDirectory:
    """The listed channels, by name."""
    channels, walk = await asyncio.gather(SlackChannel.listed_channels(), queue.get(DIRECTORY_JOB))
    return SlackChannelDirectory(
        channels=[SlackChannelOption.of(channel) for channel in channels],
        refreshing=walk is not None and walk.underway,
    )


async def ensure_directory_sync(*, by: UUID | None = None) -> None:
    """Keep the recurring walk queued; the first one runs right away."""
    if not ENV.SLACK_BOT_TOKEN.get():
        return
    await queue.schedule(
        DIRECTORY_JOB,
        cron=_WALK_SCHEDULE,
        due_at=datetime.now(UTC),
        key=DIRECTORY_JOB,
        by=by,
        replace=False,
    )


async def refresh_directory(*, by: UUID | None) -> None:
    """Walk Slack again now, unless a walk is already running."""
    await ensure_directory_sync(by=by)
    await queue.run_now(DIRECTORY_JOB, by=by)


async def sync_directory() -> None:
    """One walk; open pickers reload when it ends, whether or not it got through."""
    try:
        seen = await SlackChannel.sync_directory()
    finally:
        await Topic.SLACK_CHANNELS.invalidate()
    logger.info("Slack channel directory synced", extra={"slack_channels_seen": seen})
