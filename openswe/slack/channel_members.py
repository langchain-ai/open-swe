"""Who is in a Slack channel, by ``users.id``, cached from ``conversations.members``.

Only people with an Open SWE account are kept. A channel's members are taken in
again by a job once ``MEMBERS_TTL`` has passed since ``members_synced_at``.
"""

import logging
from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID, uuid7

from slack_sdk.errors import SlackApiError
from sqlalchemy import ForeignKey, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.jobs import queue
from openswe.jobs.queue import Job
from openswe.slack.channels import SlackChannel
from openswe.slack.http import SlackClient, slack_error
from openswe.users import User

logger = logging.getLogger(__name__)

SYNC_JOB: Final = "slack_channel_members"
MEMBERS_TTL: Final = timedelta(hours=6)
_PAGE_SIZE: Final = 1000
# Slack's answers for a channel the bot cannot list: retrying will not change them.
_UNREADABLE: Final = frozenset({"channel_not_found", "not_in_channel", "missing_scope"})


class SlackChannelMember(Base):
    __tablename__ = "slack_channel_member"

    channel_id: Mapped[str] = mapped_column(ForeignKey("slack_channel.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(default=None, init=False)
    updated_by: Mapped[UUID | None] = mapped_column(default=None, init=False)

    @classmethod
    async def joined(cls, user_id: UUID, channel_ids: Collection[str]) -> set[str]:
        """Which of ``channel_ids`` the cache says this person is in; stale channels resync."""
        wanted = {channel_id for channel_id in channel_ids if channel_id}
        if not wanted or not postgres.configured():
            return set()
        async with postgres.session() as session:
            joined = set(
                await session.scalars(
                    select(cls.channel_id).where(cls.user_id == user_id, cls.channel_id.in_(wanted))
                )
            )
            fresh = set(
                await session.scalars(
                    select(SlackChannel.id).where(
                        SlackChannel.id.in_(wanted),
                        SlackChannel.members_synced_at > datetime.now(UTC) - MEMBERS_TTL,
                    )
                )
            )
        for channel_id in sorted(wanted - fresh):
            await queue.schedule(
                SYNC_JOB,
                {"channel_id": channel_id},
                key=f"{SYNC_JOB}:{channel_id}",
                replace=False,
            )
        return joined

    @classmethod
    async def sync(cls, channel_id: str) -> None:
        """Replace the cached members of ``channel_id`` with what Slack says now."""
        slack_ids: set[str] = set()
        try:
            async with SlackClient.bot() as client:
                async for data in SlackChannel.responses(
                    lambda cursor: client.conversations_members(
                        channel=channel_id, limit=_PAGE_SIZE, cursor=cursor
                    ),
                    patient=True,
                ):
                    members = data.get("members")
                    if isinstance(members, list):
                        slack_ids.update(m for m in members if isinstance(m, str))
        except SlackApiError as exc:
            if slack_error(exc) not in _UNREADABLE:
                raise
            logger.info(
                "Slack channel members are unreadable; caching none",
                extra={"slack_channel": channel_id, "slack_error": slack_error(exc)},
            )
            slack_ids = set()
        users = await User.for_identities("slack", slack_ids)
        user_ids = {user.id for user in users.values()}
        async with postgres.session() as session:
            await SlackChannel.ensure(session, channel_id)
            gone = delete(cls).where(cls.channel_id == channel_id)
            await session.execute(gone.where(cls.user_id.not_in(user_ids)) if user_ids else gone)
            if user_ids:
                await session.execute(
                    insert(cls)
                    .values(
                        [
                            {"id": uuid7(), "channel_id": channel_id, "user_id": user_id}
                            for user_id in user_ids
                        ]
                    )
                    .on_conflict_do_nothing(index_elements=[cls.channel_id, cls.user_id])
                )
            await session.execute(
                update(SlackChannel)
                .where(SlackChannel.id == channel_id)
                .values(members_synced_at=func.clock_timestamp())
            )


async def sync_channel_members(job: Job) -> None:
    channel_id = job.payload.get("channel_id")
    if not isinstance(channel_id, str) or not channel_id:
        raise ValueError("A channel member sync needs a channel id")
    await SlackChannelMember.sync(channel_id)
