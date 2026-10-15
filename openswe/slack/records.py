"""What Open SWE keeps about Slack threads, the runs in them, and its messages there.

A Slack thread binds to one Open SWE thread. Detaching it leaves a nonce in
place of the binding, so resolving the location again names a fresh thread
instead of the one that moved away. The thread also records who its untagged
messages route from and the kickoff message its next reply replaces.

Each run started for a Slack thread keeps the last message it posted, and the
run started most recently is the thread's current one. Each bot message maps
to the run that posted it, which is how reactions and buttons on it find their
run; a bot DM also records the channel thread it was sent for.

Kickoffs and reply progress matter only while a run is going: they expire,
reads treat an expired one as absent, and writes clear a few expired ones.
"""

import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Self
from uuid import UUID, uuid4, uuid7

from langgraph_sdk.client import LangGraphClient
from sqlalchemy import (
    ColumnElement,
    ForeignKey,
    Text,
    case,
    delete,
    func,
    literal_column,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.slack.channels import SlackChannel
from openswe.slack.client import SlackThreadMappingError, normalize_slack_location
from openswe.source_context import SlackThreadRef, SourceContext
from openswe.thread_ids import slack_thread_id
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

KICKOFF_TTL = timedelta(days=7)
REPLY_FRESHNESS_TTL = timedelta(days=2)
_SWEEP_LIMIT = 100


def _live(expires_at: datetime | None) -> bool:
    return expires_at is not None and expires_at > datetime.now(UTC)


class SlackThread(Base):
    """A Slack thread: its Open SWE thread, solo-thread routing, and kickoff message."""

    __tablename__ = "slack_thread"

    channel_id: Mapped[str] = mapped_column(ForeignKey("slack_channel.id"))
    thread_ts: Mapped[str]
    thread_id: Mapped[str | None] = mapped_column(default=None)
    detach_nonce: Mapped[str | None] = mapped_column(default=None)
    # The one person whose untagged messages route to the thread, and their latest one.
    solo_user_id: Mapped[str | None] = mapped_column(default=None)
    solo_last_seen: Mapped[float | None] = mapped_column(default=None)
    # Set once someone else joined: untagged messages never route again.
    solo_blocked: Mapped[bool] = mapped_column(server_default="false", default=False)
    # Unset with ``kickoff_expires_at`` set once the kickoff message was removed.
    kickoff_ts: Mapped[str | None] = mapped_column(default=None)
    kickoff_expires_at: Mapped[datetime | None] = mapped_column(default=None)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    @property
    def solo_tracked(self) -> bool:
        return self.solo_blocked or self.solo_user_id is not None

    @property
    def kickoff_recorded(self) -> bool:
        return _live(self.kickoff_expires_at)

    @classmethod
    async def find(cls, channel_id: str, thread_ts: str) -> Self | None:
        async with postgres.session() as session:
            return await session.scalar(
                select(cls).where(cls.channel_id == channel_id, cls.thread_ts == thread_ts)
            )

    @classmethod
    async def _save(cls, channel_id: str, thread_ts: str, values: Mapping[str, object]) -> None:
        upsert = insert(cls).values(
            id=uuid7(), channel_id=channel_id, thread_ts=thread_ts, **values
        )
        async with postgres.session() as session:
            await SlackChannel.ensure(session, channel_id)
            await _clear_expired(
                session, cls, cls.kickoff_expires_at, ("kickoff_ts", "kickoff_expires_at")
            )
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[cls.channel_id, cls.thread_ts],
                    set_={
                        **{name: getattr(upsert.excluded, name) for name in values},
                        "updated_at": func.clock_timestamp(),
                    },
                )
            )

    @classmethod
    async def track_solo(
        cls, channel_id: str, thread_ts: str, user_id: str, last_seen: float
    ) -> None:
        await cls._save(
            channel_id,
            thread_ts,
            {"solo_user_id": user_id, "solo_last_seen": last_seen, "solo_blocked": False},
        )

    @classmethod
    async def block_solo(cls, channel_id: str, thread_ts: str) -> None:
        """Stop routing untagged messages in the thread for good."""
        await cls._save(
            channel_id,
            thread_ts,
            {"solo_user_id": None, "solo_last_seen": None, "solo_blocked": True},
        )

    @classmethod
    async def record_kickoff(cls, channel_id: str, thread_ts: str, kickoff_ts: str | None) -> None:
        """Remember the thread's kickoff message, or with ``None`` that it was removed."""
        await cls._save(
            channel_id,
            thread_ts,
            {
                "kickoff_ts": kickoff_ts,
                "kickoff_expires_at": func.clock_timestamp() + KICKOFF_TTL,
            },
        )


class SlackRun(Base):
    """A run started for a Slack thread, or that posted in one, and its last message there."""

    __tablename__ = "slack_run"

    channel_id: Mapped[str] = mapped_column(ForeignKey("slack_channel.id"))
    run_id: Mapped[str]
    thread_ts: Mapped[str]
    message_ts: Mapped[str | None] = mapped_column(default=None)
    trace_message_ts: Mapped[str | None] = mapped_column(default=None)
    triggering_user_id: Mapped[str | None] = mapped_column(default=None)
    agent_thread_id: Mapped[str | None] = mapped_column(default=None)
    # Unset for a run that only posted in the thread; the latest is the thread's current run.
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    # The Slack messages the run has seen, and how many of its replies were held back as stale.
    reply_seen_ts: Mapped[list[str] | None] = mapped_column(ARRAY(Text), default=None)
    reply_conflicts: Mapped[int | None] = mapped_column(default=None)
    reply_expires_at: Mapped[datetime | None] = mapped_column(default=None)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    @classmethod
    async def reply_progress(cls, channel_id: str, run_id: str) -> tuple[set[str], int]:
        """The messages the run has seen and its stale replies so far, while they last."""
        async with postgres.session() as session:
            run = await _run(session, channel_id, run_id)
        if run is None or not _live(run.reply_expires_at):
            return set(), 0
        return set(run.reply_seen_ts or ()), run.reply_conflicts or 0

    @classmethod
    async def record_reply_conflict(
        cls, channel_id: str, thread_ts: str, run_id: str, seen_ts: list[str]
    ) -> None:
        """Count one more stale reply, having seen ``seen_ts``."""
        upsert = insert(cls).values(
            id=uuid7(),
            channel_id=channel_id,
            run_id=run_id,
            thread_ts=thread_ts,
            reply_seen_ts=seen_ts,
            reply_conflicts=1,
            reply_expires_at=func.clock_timestamp() + REPLY_FRESHNESS_TTL,
        )
        async with postgres.session() as session:
            await SlackChannel.ensure(session, channel_id)
            await _clear_expired(
                session,
                cls,
                cls.reply_expires_at,
                ("reply_seen_ts", "reply_conflicts", "reply_expires_at"),
            )
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[cls.channel_id, cls.run_id],
                    set_={
                        "reply_seen_ts": upsert.excluded.reply_seen_ts,
                        "reply_conflicts": case(
                            (
                                cls.reply_expires_at > func.clock_timestamp(),
                                cls.reply_conflicts + 1,
                            ),
                            else_=1,
                        ),
                        "reply_expires_at": upsert.excluded.reply_expires_at,
                        "updated_at": func.clock_timestamp(),
                    },
                )
            )


class SlackMessage(Base):
    """A Slack message Open SWE tracks: the run behind it, the channel thread a DM was sent
    for, and the feedback reactions each person left on it."""

    __tablename__ = "slack_message"

    channel_id: Mapped[str] = mapped_column(ForeignKey("slack_channel.id"))
    message_ts: Mapped[str]
    thread_ts: Mapped[str | None] = mapped_column(default=None)
    run_id: Mapped[str | None] = mapped_column(default=None)
    triggering_user_id: Mapped[str | None] = mapped_column(default=None)
    agent_thread_id: Mapped[str | None] = mapped_column(default=None)
    origin_channel_id: Mapped[str | None] = mapped_column(
        ForeignKey("slack_channel.id"), default=None
    )
    origin_thread_ts: Mapped[str | None] = mapped_column(default=None)
    origin_subject: Mapped[str | None] = mapped_column(default=None)
    # Each Slack user's feedback reactions on the message.
    reactions: Mapped[dict[str, list[str]]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default_factory=dict
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    @classmethod
    async def toggle_reaction(
        cls, channel_id: str, message_ts: str, user_id: str, reaction: str, *, added: bool
    ) -> set[str]:
        """Add or remove ``reaction`` and return the person's reactions after the change."""
        async with postgres.session() as session:
            await SlackChannel.ensure(session, channel_id)
            reactions = await session.scalar(
                _TOGGLE_REACTION,
                {
                    "id": uuid7(),
                    "channel_id": channel_id,
                    "message_ts": message_ts,
                    "user_id": user_id,
                    "reaction": reaction,
                    "added": added,
                },
            )
        return {item for item in reactions or () if isinstance(item, str)}

    @classmethod
    async def origin(cls, dm_channel_id: str, message_ts: str) -> Self | None:
        """The DM message, when it records the channel thread it was sent for."""
        async with postgres.session() as session:
            return await session.scalar(
                select(cls).where(
                    cls.channel_id == dm_channel_id,
                    cls.message_ts == message_ts,
                    cls.origin_channel_id.is_not(None),
                )
            )

    @classmethod
    async def save_origin(
        cls, dm_channel_id: str, message_ts: str, channel_id: str, thread_ts: str, subject: str
    ) -> None:
        upsert = insert(cls).values(
            id=uuid7(),
            channel_id=dm_channel_id,
            message_ts=message_ts,
            origin_channel_id=channel_id,
            origin_thread_ts=thread_ts,
            origin_subject=subject,
        )
        async with postgres.session() as session:
            await SlackChannel.ensure(session, dm_channel_id, channel_id)
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[cls.channel_id, cls.message_ts],
                    set_={
                        "origin_channel_id": upsert.excluded.origin_channel_id,
                        "origin_thread_ts": upsert.excluded.origin_thread_ts,
                        "origin_subject": upsert.excluded.origin_subject,
                        "updated_at": func.clock_timestamp(),
                    },
                )
            )


# One statement, so concurrent reactions from the same person cannot lose one another.
_TOGGLE_REACTION = text("""
    INSERT INTO slack_message AS message (id, channel_id, message_ts, reactions)
    VALUES (
        :id, :channel_id, :message_ts,
        jsonb_build_object(
            CAST(:user_id AS text),
            CASE WHEN CAST(:added AS boolean)
                THEN jsonb_build_array(CAST(:reaction AS text)) ELSE '[]'::jsonb END
        )
    )
    ON CONFLICT (channel_id, message_ts) DO UPDATE SET
        reactions = jsonb_set(
            message.reactions,
            ARRAY[CAST(:user_id AS text)],
            (COALESCE(message.reactions -> CAST(:user_id AS text), '[]'::jsonb)
                - CAST(:reaction AS text))
            || CASE WHEN CAST(:added AS boolean)
                THEN jsonb_build_array(CAST(:reaction AS text)) ELSE '[]'::jsonb END
        ),
        updated_at = clock_timestamp()
    RETURNING reactions -> CAST(:user_id AS text)
""")


class SlackViewSuggestion(Base):
    """The options a code-channel view's select menus suggest, by action id."""

    __tablename__ = "slack_view_suggestion"

    channel_id: Mapped[str] = mapped_column(ForeignKey("slack_channel.id"))
    view_id: Mapped[str]
    actions: Mapped[JsonObject] = mapped_column(JSONB)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
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
        )
        async with postgres.session() as session:
            await SlackChannel.ensure(session, self.channel_id)
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[type(self).channel_id, type(self).view_id],
                    set_={
                        "actions": upsert.excluded.actions,
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


async def _clear_expired(
    session: AsyncSession,
    row: type[SlackThread] | type[SlackRun],
    expires_at: InstrumentedAttribute[datetime | None],
    columns: tuple[str, ...],
) -> None:
    """Clear a bounded batch of expired ``columns``; the rows hold more than what expired."""
    expired = (
        select(row.id)
        .where(expires_at <= func.clock_timestamp())
        .limit(_SWEEP_LIMIT)
        .with_for_update(skip_locked=True)
    )
    await session.execute(
        update(row)
        .where(row.id.in_(expired.scalar_subquery()))
        .values({**dict.fromkeys(columns), "updated_at": func.clock_timestamp()})
    )


_RUN_FIELDS = (
    "thread_ts",
    "message_ts",
    "triggering_user_id",
    "trace_message_ts",
    "agent_thread_id",
)
_MESSAGE_FIELDS = ("run_id", "thread_ts", "triggering_user_id", "agent_thread_id")


def _merge_run() -> dict[str, ColumnElement[object]]:
    """How an upsert of ``slack_run`` updates a row: set fields replace, unset ones keep."""
    excluded = insert(SlackRun).excluded
    return {
        **{
            field: func.coalesce(getattr(excluded, field), getattr(SlackRun, field))
            for field in _RUN_FIELDS
        },
        "started_at": func.greatest(excluded.started_at, SlackRun.started_at),
        "updated_at": func.clock_timestamp(),
    }


def _at_location(channel_id: str, thread_ts: str) -> ColumnElement[bool]:
    return (SlackThread.channel_id == channel_id) & (SlackThread.thread_ts == thread_ts)


async def lookup_slack_thread_id(channel_id: str, thread_ts: str) -> str | None:
    """The Open SWE thread bound to a Slack location."""
    channel, timestamp = normalize_slack_location(channel_id, thread_ts)
    async with postgres.session() as session:
        return await session.scalar(
            select(SlackThread.thread_id).where(_at_location(channel, timestamp))
        )


async def bind_slack_thread_id(channel_id: str, thread_ts: str, thread_id: str) -> str:
    """Bind a Slack location to an Open SWE thread unless another thread holds it."""
    channel, timestamp = normalize_slack_location(channel_id, thread_ts)
    normalized_thread_id = thread_id.strip() if isinstance(thread_id, str) else ""
    if not normalized_thread_id:
        raise SlackThreadMappingError("Open SWE thread ID is required")
    upsert = insert(SlackThread).values(
        id=uuid7(), channel_id=channel, thread_ts=timestamp, thread_id=normalized_thread_id
    )
    async with postgres.session() as session:
        await SlackChannel.ensure(session, channel)
        bound = await session.scalar(
            upsert.on_conflict_do_update(
                index_elements=[SlackThread.channel_id, SlackThread.thread_ts],
                set_={
                    "thread_id": upsert.excluded.thread_id,
                    "detach_nonce": None,
                    "updated_at": func.clock_timestamp(),
                },
                where=SlackThread.thread_id.is_(None)
                | (SlackThread.thread_id == upsert.excluded.thread_id),
            ).returning(SlackThread.thread_id)
        )
    if bound is None:
        raise SlackThreadMappingError("Slack location is already mapped to another thread")
    return normalized_thread_id


def _thread_metadata_slack_location(thread: Mapping[str, object]) -> tuple[str, str] | None:
    metadata = thread.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    location = SourceContext.from_metadata(metadata).slack_location
    if location is None:
        return None
    try:
        return normalize_slack_location(*location)
    except SlackThreadMappingError:
        return None


async def resolve_slack_thread_id(
    langgraph_client: LangGraphClient, channel_id: str, thread_ts: str
) -> str:
    """The Open SWE thread bound to a Slack location, binding one first if none is."""
    channel, timestamp = normalize_slack_location(channel_id, thread_ts)
    async with postgres.session() as session:
        mapping = (
            await session.execute(
                select(SlackThread.thread_id, SlackThread.detach_nonce).where(
                    _at_location(channel, timestamp)
                )
            )
        ).first()
    if mapping is not None and mapping.thread_id:
        return mapping.thread_id

    matches = await langgraph_client.threads.search(
        metadata={
            "source_context": SourceContext(
                slack_thread=SlackThreadRef(channel_id=channel, thread_ts=timestamp)
            ).dump()
        },
        limit=2,
    )
    matching_ids = {
        candidate
        for thread in matches or []
        if isinstance(thread, Mapping)
        and _thread_metadata_slack_location(thread) == (channel, timestamp)
        and isinstance(candidate := thread.get("thread_id") or thread.get("id"), str)
        and candidate
    }
    if len(matching_ids) > 1:
        raise SlackThreadMappingError("Multiple Open SWE threads match this Slack location")

    candidate = next(
        iter(matching_ids),
        slack_thread_id(channel, timestamp, None if mapping is None else mapping.detach_nonce),
    )
    return await bind_slack_thread_id(channel, timestamp, candidate)


async def delete_slack_thread_associations(
    channel_id: str, thread_ts: str, *, expected_thread_id: str | None = None
) -> None:
    """Detach a Slack location from its thread and forget the runs and messages there.

    Nothing changes when ``expected_thread_id`` is given and another thread holds the location.
    """
    channel, timestamp = normalize_slack_location(channel_id, thread_ts)
    async with postgres.session() as session:
        mapped_thread_id = await session.scalar(
            select(SlackThread.thread_id).where(_at_location(channel, timestamp)).with_for_update()
        )
        if expected_thread_id and mapped_thread_id and mapped_thread_id != expected_thread_id:
            return
        await SlackChannel.ensure(session, channel)
        detach = insert(SlackThread).values(
            id=uuid7(), channel_id=channel, thread_ts=timestamp, detach_nonce=str(uuid4())
        )
        await session.execute(
            detach.on_conflict_do_update(
                index_elements=[SlackThread.channel_id, SlackThread.thread_ts],
                set_={
                    "thread_id": None,
                    "detach_nonce": detach.excluded.detach_nonce,
                    "updated_at": func.clock_timestamp(),
                },
            )
        )
        await session.execute(
            delete(SlackRun).where(SlackRun.channel_id == channel, SlackRun.thread_ts == timestamp)
        )
        in_thread = (SlackMessage.channel_id == channel) & (SlackMessage.thread_ts == timestamp)
        # A message that holds nothing but its run goes; a DM origin or reactions stay.
        await session.execute(
            delete(SlackMessage).where(
                in_thread,
                SlackMessage.origin_channel_id.is_(None),
                SlackMessage.reactions == literal_column("'{}'::jsonb"),
            )
        )
        await session.execute(
            update(SlackMessage)
            .where(in_thread)
            .values(
                **dict.fromkeys(_MESSAGE_FIELDS),
                updated_at=func.clock_timestamp(),
            )
        )


async def _current_run(session: AsyncSession, channel_id: str, thread_ts: str) -> SlackRun | None:
    return await session.scalar(
        select(SlackRun)
        .where(
            SlackRun.channel_id == channel_id,
            SlackRun.thread_ts == thread_ts,
            SlackRun.started_at.is_not(None),
        )
        .order_by(SlackRun.started_at.desc())
        .limit(1)
    )


async def _run(session: AsyncSession, channel_id: str, run_id: str) -> SlackRun | None:
    return await session.scalar(
        select(SlackRun).where(SlackRun.channel_id == channel_id, SlackRun.run_id == run_id)
    )


async def _upsert_run(
    session: AsyncSession,
    channel_id: str,
    run_id: str,
    values: Mapping[str, str | None],
    *,
    start: bool = False,
) -> SlackRun:
    """Save the run's set ``values``; ``start`` makes it the thread's current run."""
    upsert = insert(SlackRun).values(
        id=uuid7(),
        channel_id=channel_id,
        run_id=run_id,
        started_at=func.clock_timestamp() if start else None,
        **values,
    )
    saved = await session.scalars(
        upsert.on_conflict_do_update(
            index_elements=[SlackRun.channel_id, SlackRun.run_id],
            set_=_merge_run(),
        ).returning(SlackRun),
        execution_options={"populate_existing": True},
    )
    return saved.one()


def _inherited(field: str, *runs: SlackRun | None) -> str | None:
    return next((value for run in runs if run is not None and (value := getattr(run, field))), None)


async def _upsert_message(
    session: AsyncSession, channel_id: str, message_ts: str, values: Mapping[str, str | None]
) -> None:
    upsert = insert(SlackMessage).values(
        id=uuid7(), channel_id=channel_id, message_ts=message_ts, **values
    )
    await session.execute(
        upsert.on_conflict_do_update(
            index_elements=[SlackMessage.channel_id, SlackMessage.message_ts],
            set_={
                **{field: getattr(upsert.excluded, field) for field in _MESSAGE_FIELDS},
                "updated_at": func.clock_timestamp(),
            },
        )
    )


async def store_slack_run_mapping(
    channel_id: str,
    thread_ts: str,
    run_id: str,
    *,
    message_ts: str | None = None,
    triggering_user_id: str | None = None,
    trace_message_ts: str | None = None,
    agent_thread_id: str | None = None,
) -> None:
    """Make ``run_id`` the Slack thread's current run, keeping the thread's trace reply."""
    try:
        async with postgres.session() as session:
            await SlackChannel.ensure(session, channel_id)
            if not trace_message_ts and (
                current := await _current_run(session, channel_id, thread_ts)
            ):
                trace_message_ts = current.trace_message_ts
            run = await _upsert_run(
                session,
                channel_id,
                run_id,
                {
                    "thread_ts": thread_ts,
                    "message_ts": message_ts,
                    "triggering_user_id": triggering_user_id,
                    "trace_message_ts": trace_message_ts,
                    "agent_thread_id": agent_thread_id,
                },
                start=True,
            )
            if message_ts:
                await _upsert_message(
                    session,
                    channel_id,
                    message_ts,
                    {
                        "run_id": run_id,
                        "thread_ts": thread_ts,
                        "triggering_user_id": run.triggering_user_id,
                        "agent_thread_id": run.agent_thread_id,
                    },
                )
    except Exception:
        logger.exception(
            "Failed to store Slack run mapping",
            extra={"slack_channel": channel_id, "slack_thread_ts": thread_ts, "run_id": run_id},
        )


async def store_slack_message_run_mapping(
    channel_id: str,
    thread_ts: str,
    message_ts: str,
    *,
    run_id: str | None = None,
    triggering_user_id: str | None = None,
) -> None:
    """Map a bot message to the run that posted it, the thread's current run by default."""
    try:
        async with postgres.session() as session:
            await SlackChannel.ensure(session, channel_id)
            current = await _current_run(session, channel_id, thread_ts)
            run = await _run(session, channel_id, run_id) if run_id else current
            resolved_run_id = run_id or (current.run_id if current else None)
            if not resolved_run_id:
                logger.debug(
                    "No Slack run mapping found",
                    extra={"slack_channel": channel_id, "slack_thread_ts": thread_ts},
                )
                return
            # The run's own fields win; a run that never started here borrows the thread's.
            agent_thread_id = _inherited("agent_thread_id", run, current)
            triggering_user_id = triggering_user_id or _inherited(
                "triggering_user_id", run, current
            )
            await _upsert_message(
                session,
                channel_id,
                message_ts,
                {
                    "run_id": resolved_run_id,
                    "thread_ts": thread_ts,
                    "triggering_user_id": triggering_user_id,
                    "agent_thread_id": agent_thread_id,
                },
            )
            await _upsert_run(
                session,
                channel_id,
                resolved_run_id,
                {
                    "thread_ts": thread_ts,
                    "message_ts": message_ts,
                    "triggering_user_id": triggering_user_id,
                    "trace_message_ts": _inherited("trace_message_ts", run, current),
                    "agent_thread_id": agent_thread_id,
                },
            )
    except Exception:
        logger.exception(
            "Failed to store Slack message run mapping",
            extra={"slack_channel": channel_id, "slack_message_ts": message_ts},
        )


async def lookup_slack_run_message_mapping(channel_id: str, run_id: str) -> SlackRun | None:
    """The run, with the latest Slack message it posted."""
    try:
        async with postgres.session() as session:
            return await _run(session, channel_id, run_id)
    except Exception:
        logger.exception(
            "Failed to look up Slack run mapping",
            extra={"slack_channel": channel_id, "run_id": run_id},
        )
        return None


async def lookup_slack_thread_run_mapping(channel_id: str, thread_ts: str) -> SlackRun | None:
    """The Slack thread's current run."""
    try:
        async with postgres.session() as session:
            return await _current_run(session, channel_id, thread_ts)
    except Exception:
        logger.exception(
            "Failed to look up Slack thread run mapping",
            extra={"slack_channel": channel_id, "slack_thread_ts": thread_ts},
        )
        return None


async def lookup_slack_run_mapping(channel_id: str, message_ts: str) -> SlackMessage | None:
    """The run behind a bot message."""
    try:
        async with postgres.session() as session:
            return await session.scalar(
                select(SlackMessage).where(
                    SlackMessage.channel_id == channel_id,
                    SlackMessage.message_ts == message_ts,
                    SlackMessage.run_id.is_not(None),
                )
            )
    except Exception:
        logger.exception(
            "Failed to look up Slack message run mapping",
            extra={"slack_channel": channel_id, "slack_message_ts": message_ts},
        )
        return None
