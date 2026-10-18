"""Slacklets: Slack messages Open SWE keeps live, whose state changes only by logged commands.

The database owns each slacklet's state. A change is appended to its command
log first, then applied to the state, and queued for the thread's agent; the
slacklet message is the one thing edited, to show the state after the latest
command. The log itself stays out of Slack.
"""

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Self
from uuid import UUID, uuid7

from pydantic import TypeAdapter
from sqlalchemy import ColumnElement, ForeignKey, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.prompts import prompt
from openswe.run_config import RunConfig
from openswe.slack.blocks import block_payload
from openswe.slack.client import (
    delete_slack_message,
    post_slack_thread_reply_with_ts,
    update_slack_message,
)
from openswe.slack.http import SlackRequestError
from openswe.slack.records import SlackThread
from openswe.slack.thread_notes import note_for_thread_owner
from openswe.slacklets.kind import Entry, SlackletCommand, SlackletState
from openswe.ui_invalidations import Topic
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)


class SlackletCommandRecord(Base):
    __tablename__ = "slacklet_command"

    slacklet_id: Mapped[UUID] = mapped_column(ForeignKey("slacklet.id", ondelete="CASCADE"))
    seq: Mapped[int]
    command: Mapped[JsonObject] = mapped_column(JSONB)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


class Slacklet(Base):
    """One slacklet: its kind, what it is about, and the message showing its state.

    ``subject`` names what the slacklet follows in its kind's own terms, such as
    ``owner/repo/7`` for a pull request; a thread has at most one slacklet per
    kind and subject.
    """

    __tablename__ = "slacklet"

    kind: Mapped[str]
    subject: Mapped[str]
    slack_thread_id: Mapped[UUID] = mapped_column(ForeignKey("slack_thread.id", ondelete="CASCADE"))
    message_ts: Mapped[str]
    state: Mapped[JsonObject] = mapped_column(JSONB)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    slack_thread: Mapped[SlackThread] = relationship(init=False, lazy="joined", innerjoin=True)

    @property
    def location(self) -> tuple[str, str]:
        return self.slack_thread.channel_id, self.slack_thread.thread_ts

    @classmethod
    async def get(cls, slacklet_id: UUID) -> Self | None:
        async with postgres.session() as session:
            return await session.get(cls, slacklet_id)

    @classmethod
    async def of_kind[S: SlackletState, C: SlackletCommand](
        cls, kind: SlackletKind[S, C], *where: ColumnElement[bool]
    ) -> list[Self]:
        async with postgres.session() as session:
            return list(
                (await session.scalars(select(cls).where(cls.kind == kind.name, *where))).unique()
            )

    @classmethod
    async def attach[S: SlackletState, C: SlackletCommand](
        cls,
        kind: SlackletKind[S, C],
        subject: str,
        location: tuple[str, str],
        first: C,
        *,
        created_by: UUID | None,
    ) -> tuple[Self, list[Entry[S, C]]]:
        """Post a slacklet created by command ``first``, or find the thread's existing one.

        Returns the slacklet and every command applied to it so far.
        """
        channel_id, thread_ts = location
        async with postgres.session() as session:
            slack_thread_id = await SlackThread.ensure(session, channel_id, thread_ts)
            existing = await cls._find(session, kind, subject, slack_thread_id)
        if existing is not None:
            return existing, await existing.entries(kind)

        state = kind.apply(None, first)
        entry = Entry(seq=1, command=first, state=state)
        message_ts = await post_slack_thread_reply_with_ts(
            channel_id,
            thread_ts,
            state.fallback_text(),
            blocks=block_payload(state.blocks(seq=entry.seq, op=first.op)),
            unfurl_links=False,
            unfurl_media=False,
        )
        try:
            async with postgres.session() as session:
                slacklet = cls(
                    kind=kind.name,
                    subject=subject,
                    slack_thread_id=slack_thread_id,
                    message_ts=message_ts,
                    state=state.model_dump(mode="json"),
                    created_by=created_by,
                    updated_by=created_by,
                )
                session.add(slacklet)
                await session.flush()
                slacklet._record(session, entry, created_by=created_by)
        except IntegrityError:
            if not await delete_slack_message(channel_id, message_ts):
                logger.warning("Left a duplicate slacklet message", extra={"slack_ts": message_ts})
            async with postgres.session() as session:
                raced = await cls._find(session, kind, subject, slack_thread_id)
            if raced is None:
                raise
            return raced, await raced.entries(kind)
        loaded = await cls.get(slacklet.id)
        if loaded is None:
            raise LookupError("The slacklet vanished right after it was attached")
        return loaded, [entry]

    @classmethod
    async def _find[S: SlackletState, C: SlackletCommand](
        cls, session: AsyncSession, kind: SlackletKind[S, C], subject: str, slack_thread_id: UUID
    ) -> Self | None:
        return await session.scalar(
            select(cls).where(
                cls.kind == kind.name,
                cls.subject == subject,
                cls.slack_thread_id == slack_thread_id,
            )
        )

    async def entries[S: SlackletState, C: SlackletCommand](
        self, kind: SlackletKind[S, C]
    ) -> list[Entry[S, C]]:
        """Every command applied so far, replayed from the first."""
        return [entry for entry, _ in await self.history(kind)]

    async def history[S: SlackletState, C: SlackletCommand](
        self, kind: SlackletKind[S, C]
    ) -> list[tuple[Entry[S, C], SlackletCommandRecord]]:
        """Each stored command, oldest first, with the state replaying it left."""
        async with postgres.session() as session:
            records = list(
                await session.scalars(
                    select(SlackletCommandRecord)
                    .where(SlackletCommandRecord.slacklet_id == self.id)
                    .order_by(SlackletCommandRecord.seq)
                )
            )
        return list(zip(kind.replay(records), records, strict=True))

    async def advance[S: SlackletState, C: SlackletCommand](
        self, kind: SlackletKind[S, C], plan: Callable[[S], list[C]]
    ) -> list[Entry[S, C]]:
        """Append the commands ``plan`` picks for the current state, and publish the result.

        The row stays locked from reading the state through updating the
        message, so concurrent changes apply one after another and the message
        never shows an older state than the log.
        """
        async with postgres.session() as session:
            locked = await session.scalar(
                select(Slacklet).where(Slacklet.id == self.id).with_for_update(of=Slacklet)
            )
            if locked is None:
                return []
            state = kind.state.model_validate(locked.state)
            seq = (
                await session.scalar(
                    select(func.max(SlackletCommandRecord.seq)).where(
                        SlackletCommandRecord.slacklet_id == self.id
                    )
                )
                or 0
            )
            applied: list[Entry[S, C]] = []
            for command in plan(state):
                seq += 1
                state = kind.apply(state, command)
                entry = Entry(seq=seq, command=command, state=state)
                self._record(session, entry, created_by=None)
                applied.append(entry)
            if not applied:
                return []
            await session.execute(
                update(Slacklet)
                .where(Slacklet.id == self.id)
                .values(
                    state=state.model_dump(mode="json"),
                    updated_by=None,
                    updated_at=func.clock_timestamp(),
                )
            )
            await session.flush()
            await self._show(applied[-1])
            await Topic.SLACKLETS.invalidate(session, key=str(self.id))
        await note_for_thread_owner(*self.location, transcript(applied))
        return applied

    def _record(self, session: AsyncSession, entry: Entry, *, created_by: UUID | None) -> None:
        session.add(
            SlackletCommandRecord(
                slacklet_id=self.id,
                seq=entry.seq,
                command=entry.command.model_dump(mode="json"),
                created_by=created_by,
                updated_by=created_by,
            )
        )

    async def _show(self, last: Entry) -> None:
        """Edit the slacklet message to show the state ``last`` left."""
        channel_id, _ = self.location
        try:
            await update_slack_message(
                channel_id,
                self.message_ts,
                last.state.fallback_text(),
                blocks=block_payload(last.state.blocks(seq=last.seq, op=last.command.op)),
                unfurl_links=False,
                unfurl_media=False,
            )
        except SlackRequestError as exc:
            logger.warning(
                "Updating a slacklet message failed",
                extra={"slacklet_id": str(self.id), "seq": last.seq, "slack_error": exc.code},
            )


@dataclass(frozen=True, slots=True)
class SlackletKind[S: SlackletState, C: SlackletCommand]:
    """A kind of slacklet: its state, its commands, and how a command transforms the state.

    ``start`` turns what the agent asked to follow into the slacklet's
    ``subject`` and its first command, raising ``SlackletRefused`` when the run
    may not follow it. ``apply`` receives ``None`` only for that first command,
    which must create the state.
    """

    name: str
    state: type[S]
    commands: TypeAdapter[C]
    apply: Callable[[S | None, C], S]
    start: Callable[[str, RunConfig], Awaitable[tuple[str, C]]]

    def parse(self, command: JsonObject) -> C:
        return self.commands.validate_python(command)

    def replay(self, records: Sequence[SlackletCommandRecord]) -> list[Entry[S, C]]:
        """Each stored command, in order, with the state applying it left."""
        entries: list[Entry[S, C]] = []
        state: S | None = None
        for record in records:
            command = self.parse(record.command)
            state = self.apply(state, command)
            entries.append(Entry(seq=record.seq, command=command, state=state))
        return entries

    async def begin(
        self,
        subject: str,
        cfg: RunConfig,
        location: tuple[str, str],
        *,
        created_by: UUID | None,
    ) -> tuple[Slacklet, Sequence[Entry[S, C]]]:
        """Start following ``subject`` in the thread at ``location``, or find the one already there."""
        key, first = await self.start(subject, cfg)
        return await Slacklet.attach(self, key, location, first, created_by=created_by)

    async def history(
        self, slacklet: Slacklet
    ) -> Sequence[tuple[Entry[S, C], SlackletCommandRecord]]:
        return await slacklet.history(self)


class AnySlackletKind(Protocol):
    """A slacklet kind seen without its state and command types, as the registry holds them."""

    @property
    def name(self) -> str: ...

    async def begin(
        self,
        subject: str,
        cfg: RunConfig,
        location: tuple[str, str],
        *,
        created_by: UUID | None,
    ) -> tuple[Slacklet, Sequence[Entry[SlackletState, SlackletCommand]]]: ...

    async def history(
        self, slacklet: Slacklet
    ) -> Sequence[tuple[Entry[SlackletState, SlackletCommand], SlackletCommandRecord]]: ...


def transcript[S: SlackletState, C: SlackletCommand](entries: Sequence[Entry[S, C]]) -> str:
    """What the thread's agent reads about ``entries``: each command, then the state."""
    last = entries[-1]
    return prompt(
        "slacklets/commands",
        slacklet=last.state.describe(),
        entries=[
            {"seq": entry.seq, "command": entry.command.model_dump_json()} for entry in entries
        ],
        state=last.state.model_dump_json(),
    )
