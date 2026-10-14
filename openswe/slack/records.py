"""Slack state that outlives a webhook: reaction feedback, solo-thread routing, reply
progress, DM origins, and code-channel view suggestions.

Each row is keyed by the Slack location it describes through a unique index,
which every write upserts on. Reply progress and kickoff markers matter only
while a run is going, so they expire, and each write sweeps a few expired rows.
"""

from datetime import datetime, timedelta
from typing import Self
from uuid import UUID, uuid7

from sqlalchemy import ColumnElement, ForeignKey, Text, case, delete, func, select
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.utils.json_types import JsonObject

KICKOFF_TTL = timedelta(days=7)
REPLY_FRESHNESS_TTL = timedelta(days=2)
_SWEEP_LIMIT = 100


def _expires_after(ttl: timedelta) -> ColumnElement[datetime]:
    return func.clock_timestamp() + ttl


async def _sweep(
    session: AsyncSession, row: type[SlackKickoff] | type[SlackReplyFreshness]
) -> None:
    expired = (
        select(row.id)
        .where(row.expires_at <= func.clock_timestamp())
        .limit(_SWEEP_LIMIT)
        .with_for_update(skip_locked=True)
    )
    await session.execute(delete(row).where(row.id.in_(expired.scalar_subquery())))


class SlackReactionState(Base):
    """The feedback reactions one person has left on one bot message."""

    __tablename__ = "slack_reaction_state"

    channel_id: Mapped[str]
    message_ts: Mapped[str]
    user_id: Mapped[str]
    reactions: Mapped[list[str]] = mapped_column(ARRAY(Text), default_factory=list)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    @classmethod
    async def toggle(
        cls, channel_id: str, message_ts: str, user_id: str, reaction: str, *, added: bool
    ) -> set[str]:
        """Add or remove ``reaction`` and return the person's reactions after the change."""
        upsert = insert(cls).values(
            id=uuid7(),
            channel_id=channel_id,
            message_ts=message_ts,
            user_id=user_id,
            reactions=[reaction] if added else [],
        )
        without = func.array_remove(cls.reactions, reaction)
        async with postgres.session() as session:
            reactions = await session.scalar(
                upsert.on_conflict_do_update(
                    index_elements=[cls.channel_id, cls.message_ts, cls.user_id],
                    set_={
                        "reactions": func.array_append(without, reaction) if added else without,
                        "updated_at": func.clock_timestamp(),
                    },
                ).returning(cls.reactions)
            )
        return set(reactions or ())


class SlackSoloThread(Base):
    """Who a Slack thread routes untagged messages from: the one person in it, or no one
    (``user_id`` unset) once someone else joined."""

    __tablename__ = "slack_solo_thread"

    channel_id: Mapped[str]
    thread_ts: Mapped[str]
    user_id: Mapped[str | None] = mapped_column(default=None)
    # The latest message timestamp seen from ``user_id``.
    last_seen: Mapped[float | None] = mapped_column(default=None)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    @property
    def blocked(self) -> bool:
        return self.user_id is None

    @classmethod
    async def find(cls, channel_id: str, thread_ts: str) -> Self | None:
        async with postgres.session() as session:
            return await session.scalar(
                select(cls).where(cls.channel_id == channel_id, cls.thread_ts == thread_ts)
            )

    @classmethod
    async def block(cls, channel_id: str, thread_ts: str) -> None:
        """Stop routing untagged messages in the thread for good."""
        await cls(channel_id=channel_id, thread_ts=thread_ts).save()

    async def save(self) -> None:
        upsert = insert(type(self)).values(
            id=self.id,
            channel_id=self.channel_id,
            thread_ts=self.thread_ts,
            user_id=self.user_id,
            last_seen=self.last_seen,
        )
        async with postgres.session() as session:
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[type(self).channel_id, type(self).thread_ts],
                    set_={
                        "user_id": upsert.excluded.user_id,
                        "last_seen": upsert.excluded.last_seen,
                        "updated_at": func.clock_timestamp(),
                    },
                )
            )


class SlackKickoff(Base):
    """The kickoff message a Slack thread's first run posted, which its next reply replaces;
    ``kickoff_ts`` is unset once it was removed."""

    __tablename__ = "slack_kickoff"

    channel_id: Mapped[str]
    thread_ts: Mapped[str]
    expires_at: Mapped[datetime]
    kickoff_ts: Mapped[str | None] = mapped_column(default=None)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    @property
    def removed(self) -> bool:
        return self.kickoff_ts is None

    @classmethod
    async def find(cls, channel_id: str, thread_ts: str) -> Self | None:
        async with postgres.session() as session:
            return await session.scalar(
                select(cls).where(
                    cls.channel_id == channel_id,
                    cls.thread_ts == thread_ts,
                    cls.expires_at > func.clock_timestamp(),
                )
            )

    @classmethod
    async def record(cls, channel_id: str, thread_ts: str, kickoff_ts: str | None) -> None:
        """Remember the thread's kickoff message, or with ``None`` that it was removed."""
        upsert = insert(cls).values(
            id=uuid7(),
            channel_id=channel_id,
            thread_ts=thread_ts,
            kickoff_ts=kickoff_ts,
            expires_at=_expires_after(KICKOFF_TTL),
        )
        async with postgres.session() as session:
            await _sweep(session, cls)
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[cls.channel_id, cls.thread_ts],
                    set_={
                        "kickoff_ts": upsert.excluded.kickoff_ts,
                        "expires_at": upsert.excluded.expires_at,
                        "updated_at": func.clock_timestamp(),
                    },
                )
            )


class SlackReplyFreshness(Base):
    """The Slack messages a run has seen, and how many of its replies were held back as stale."""

    __tablename__ = "slack_reply_freshness"

    run_id: Mapped[str]
    expires_at: Mapped[datetime]
    seen_ts: Mapped[list[str]] = mapped_column(ARRAY(Text), default_factory=list)
    conflicts: Mapped[int] = mapped_column(default=0)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    @classmethod
    async def find(cls, run_id: str) -> Self | None:
        async with postgres.session() as session:
            return await session.scalar(
                select(cls).where(cls.run_id == run_id, cls.expires_at > func.clock_timestamp())
            )

    @classmethod
    async def record_conflict(cls, run_id: str, seen_ts: list[str]) -> None:
        """Count one more stale reply, having seen ``seen_ts``."""
        upsert = insert(cls).values(
            id=uuid7(),
            run_id=run_id,
            seen_ts=seen_ts,
            conflicts=1,
            expires_at=_expires_after(REPLY_FRESHNESS_TTL),
        )
        async with postgres.session() as session:
            await _sweep(session, cls)
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[cls.run_id],
                    set_={
                        "seen_ts": upsert.excluded.seen_ts,
                        "conflicts": case(
                            (cls.expires_at > func.clock_timestamp(), cls.conflicts + 1), else_=1
                        ),
                        "expires_at": upsert.excluded.expires_at,
                        "updated_at": func.clock_timestamp(),
                    },
                )
            )


class SlackDmOrigin(Base):
    """The Slack thread a bot DM was sent on behalf of."""

    __tablename__ = "slack_dm_origin"

    dm_channel_id: Mapped[str]
    message_ts: Mapped[str]
    channel_id: Mapped[str]
    thread_ts: Mapped[str]
    subject: Mapped[str] = mapped_column(default="")
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    @classmethod
    async def find(cls, dm_channel_id: str, message_ts: str) -> Self | None:
        async with postgres.session() as session:
            return await session.scalar(
                select(cls).where(cls.dm_channel_id == dm_channel_id, cls.message_ts == message_ts)
            )

    async def save(self) -> None:
        upsert = insert(type(self)).values(
            id=self.id,
            dm_channel_id=self.dm_channel_id,
            message_ts=self.message_ts,
            channel_id=self.channel_id,
            thread_ts=self.thread_ts,
            subject=self.subject,
        )
        async with postgres.session() as session:
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[type(self).dm_channel_id, type(self).message_ts],
                    set_={
                        "channel_id": upsert.excluded.channel_id,
                        "thread_ts": upsert.excluded.thread_ts,
                        "subject": upsert.excluded.subject,
                        "updated_at": func.clock_timestamp(),
                    },
                )
            )


class SlackViewSuggestions(Base):
    """The options a code-channel view's select menus suggest, by action id."""

    __tablename__ = "slack_view_suggestions"

    channel_id: Mapped[str]
    view_id: Mapped[str]
    actions: Mapped[JsonObject] = mapped_column(JSONB)
    # Set while the row is as the LangGraph Store import copied it; any other write clears it.
    copied_from_store: Mapped[bool] = mapped_column(server_default="false", default=False)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    @classmethod
    async def actions_for(cls, channel_id: str, view_id: str) -> JsonObject:
        async with postgres.session() as session:
            actions = await session.scalar(
                select(cls.actions).where(cls.channel_id == channel_id, cls.view_id == view_id)
            )
        return actions or {}

    async def save(self) -> None:
        upsert = insert(type(self)).values(
            id=self.id,
            channel_id=self.channel_id,
            view_id=self.view_id,
            actions=self.actions,
            copied_from_store=self.copied_from_store,
        )
        async with postgres.session() as session:
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[type(self).channel_id, type(self).view_id],
                    set_={
                        "actions": upsert.excluded.actions,
                        "copied_from_store": upsert.excluded.copied_from_store,
                        "updated_at": func.clock_timestamp(),
                    },
                )
            )

    @classmethod
    async def discard(cls, channel_id: str, view_id: str) -> None:
        async with postgres.session() as session:
            await session.execute(
                delete(cls).where(cls.channel_id == channel_id, cls.view_id == view_id)
            )
