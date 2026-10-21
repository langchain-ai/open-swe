"""Event queues: following thread transcripts from outside any thread.

A remote MCP client cannot hold a stream open between calls, so it declares the
threads it wants once, then drains a server-side queue with one bounded long
poll at a time. The queue keeps a cursor per thread into ``thread_event``.
Draining reads past each cursor and advances it, so the queue id is all a
stateless caller has to carry.

Waking works like the transcript stream: subscribe to the listener first, then
read, so an append landing between the two still ends the wait. A missed
notification only costs latency, because each wait also re-reads on a tick.
"""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Final, Literal, Self, get_args
from uuid import UUID, uuid7

from pydantic import BaseModel, JsonValue
from sqlalchemy import ARRAY, ForeignKey, Text, bindparam, delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.threads.summary import thread_is_readable
from openswe.transcript import listener
from openswe.transcript.events import StoredEvent, ThreadStatus, TranscriptEvent

type DropReason = Literal["deleted", "revoked"]

EVENT_TYPES: Final = frozenset(
    body.model_fields["type"].default for body in get_args(get_args(TranscriptEvent.__value__)[0])
)
DEFAULT_EVENT_TYPES: Final = (
    "turn.requested",
    "turn.queued",
    "turn.started",
    "turn.completed",
    "turn.failed",
    "turn.interrupted",
    "message.completed",
)
MAX_THREADS: Final = 20
MAX_EVENTS: Final = 100
_IDLE: Final = timedelta(hours=1)
_TICK_SECONDS: Final = 5.0
_MAX_FIELD_CHARS: Final = 4_000
# Already top-level on a queued event, or bulk a follower has no use for.
_OMITTED_FIELDS: Final = frozenset({"type", "turn_id", "run_id", "namespace", "reasoning", "usage"})

_THREAD = text("SELECT metadata, version, status FROM thread WHERE thread_id = :thread_id")
_EVENTS = text(
    """
    SELECT thread_id, version, event_id, event_type, schema_version, run_id,
           turn_id, command_id, actor_kind, occurred_at, payload
    FROM thread_event
    WHERE thread_id = :thread_id AND version > :after
      AND event_type = ANY(:event_types)
      AND COALESCE(jsonb_array_length(payload -> 'namespace'), 0) = 0
    ORDER BY version
    LIMIT :limit
    """
).bindparams(bindparam("event_types", type_=ARRAY(Text)))


class QueueNotFound(LookupError):
    """No unexpired queue with this id belongs to the caller."""


class ThreadNotTranscribed(LookupError):
    """The thread has no event log to follow."""


class QueueFull(ValueError):
    """The queue already follows as many threads as it may."""


class ThreadPosition(BaseModel):
    """Where a queue stands on one thread, and whether that thread is still working."""

    thread_id: str
    status: ThreadStatus
    cursor: int


class DroppedThread(BaseModel):
    """A thread the queue stopped following: its transcript is gone, or the caller lost access."""

    thread_id: str
    reason: DropReason


class QueuedEvent(BaseModel):
    """One ``thread_event`` row as a follower reads it, long fields cut short."""

    thread_id: str
    version: int
    type: str
    occurred_at: datetime
    actor_kind: str
    run_id: str | None
    turn_id: UUID | None
    data: dict[str, JsonValue]

    @classmethod
    def of(cls, event: StoredEvent) -> Self:
        return cls(
            thread_id=event.thread_id,
            version=event.version,
            type=event.event_type,
            occurred_at=event.occurred_at,
            actor_kind=event.actor_kind,
            run_id=event.run_id,
            turn_id=event.turn_id,
            data={
                key: _bounded(value)
                for key, value in event.payload.items()
                if key not in _OMITTED_FIELDS and value not in (None, [], {})
            },
        )


class Drained(BaseModel):
    events: list[QueuedEvent]
    threads: list[ThreadPosition]
    dropped: list[DroppedThread]
    has_more: bool


class EventQueueThread(Base):
    __tablename__ = "event_queue_thread"

    queue_id: Mapped[UUID] = mapped_column(ForeignKey("event_queue.id", ondelete="CASCADE"))
    thread_id: Mapped[str]
    after_version: Mapped[int]
    event_types: Mapped[list[str]] = mapped_column(ARRAY(Text))
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


class EventQueue(Base):
    __tablename__ = "event_queue"

    expires_at: Mapped[datetime]
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    threads: Mapped[list[EventQueueThread]] = relationship(
        init=False, lazy="selectin", order_by=EventQueueThread.created_at
    )

    @classmethod
    async def open(cls, owner: UUID, queue_id: UUID | None = None) -> Self:
        """``owner``'s queue ``queue_id``, or a new one when no id is given."""
        if queue_id is not None:
            return await cls.owned(owner, queue_id)
        async with postgres.session() as session:
            await session.execute(delete(cls).where(cls.expires_at <= func.clock_timestamp()))
            queue = cls(
                expires_at=datetime.now(UTC) + _IDLE,
                created_by=owner,
                updated_by=owner,
            )
            session.add(queue)
            await session.flush()
            return await session.get_one(cls, queue.id, populate_existing=True)

    @classmethod
    async def owned(cls, owner: UUID, queue_id: UUID) -> Self:
        async with postgres.session() as session:
            queue = await session.scalar(
                select(cls).where(
                    cls.id == queue_id,
                    cls.created_by == owner,
                    cls.expires_at > func.clock_timestamp(),
                )
            )
        if queue is None:
            raise QueueNotFound(str(queue_id))
        return queue

    async def follow(
        self, thread_id: str, event_types: Sequence[str], owner: UUID
    ) -> ThreadPosition:
        """Start following ``thread_id`` from its newest event.

        A thread the queue already follows keeps its cursor and takes the new
        ``event_types``, so subscribing twice never skips or repeats an event.
        """
        async with postgres.session() as session:
            row = (await session.execute(_THREAD, {"thread_id": thread_id})).mappings().first()
            if row is None:
                raise ThreadNotTranscribed(thread_id)
            followed = next((t for t in self.threads if t.thread_id == thread_id), None)
            if followed is None:
                if len(self.threads) >= MAX_THREADS:
                    raise QueueFull(f"A queue follows at most {MAX_THREADS} threads")
                followed = EventQueueThread(
                    queue_id=self.id,
                    thread_id=thread_id,
                    after_version=int(row["version"]),
                    event_types=list(event_types),
                    created_by=owner,
                    updated_by=owner,
                )
                session.add(followed)
                self.threads.append(followed)
            else:
                await session.execute(
                    update(EventQueueThread)
                    .where(EventQueueThread.id == followed.id)
                    .values(
                        event_types=list(event_types),
                        updated_by=owner,
                        updated_at=func.clock_timestamp(),
                    )
                )
                followed.event_types = list(event_types)
            await self._extend(session, owner)
        return ThreadPosition(
            thread_id=thread_id, status=row["status"], cursor=followed.after_version
        )

    async def drain(
        self, owner: UUID, *, login: str, email: str | None, wait: float, limit: int
    ) -> Drained:
        """Wait up to ``wait`` seconds for events past the cursors, then hand them out.

        Returns at once when there already are some, or when a thread was
        dropped. The cursors advance past what is returned, and past events of
        types the queue skips.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        async with self._notifications() as woken:
            while True:
                woken.clear()
                drained, cursors = await self._read(login, email, limit)
                remaining = deadline - loop.time()
                if drained.events or drained.dropped or remaining <= 0:
                    break
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(woken.wait(), min(remaining, _TICK_SECONDS))
        await self._advance(owner, cursors, drained.dropped)
        return drained

    @contextlib.asynccontextmanager
    async def _notifications(self) -> AsyncIterator[asyncio.Event]:
        """An event set whenever any followed thread's log grows."""
        woken = asyncio.Event()

        async def forward(notifications: AsyncIterator[int]) -> None:
            async for _version in notifications:
                woken.set()

        async with contextlib.AsyncExitStack() as stack:
            tasks = [
                asyncio.create_task(
                    forward(await stack.enter_async_context(listener.subscribe(t.thread_id)))
                )
                for t in self.threads
            ]
            try:
                yield woken
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _read(
        self, login: str, email: str | None, limit: int
    ) -> tuple[Drained, dict[UUID, int]]:
        """Everything past the cursors that fits ``limit``, in one database snapshot."""
        events: list[QueuedEvent] = []
        positions: list[ThreadPosition] = []
        dropped: list[DroppedThread] = []
        cursors: dict[UUID, int] = {}
        budget = limit
        async with postgres.snapshot_transaction() as conn:
            for followed in self.threads:
                result = await conn.execute(_THREAD, {"thread_id": followed.thread_id})
                row = result.mappings().first()
                if row is None:
                    dropped.append(DroppedThread(thread_id=followed.thread_id, reason="deleted"))
                    continue
                if not thread_is_readable(row["metadata"], login, email):
                    dropped.append(DroppedThread(thread_id=followed.thread_id, reason="revoked"))
                    continue
                cursor = followed.after_version
                if budget > 0:
                    rows = (
                        await conn.execute(
                            _EVENTS,
                            {
                                "thread_id": followed.thread_id,
                                "after": cursor,
                                "event_types": followed.event_types,
                                "limit": budget,
                            },
                        )
                    ).mappings()
                    page = [StoredEvent.model_validate(dict(event)) for event in rows]
                    events.extend(QueuedEvent.of(event) for event in page)
                    budget -= len(page)
                    cursor = page[-1].version if budget == 0 and page else int(row["version"])
                    cursors[followed.id] = cursor
                positions.append(
                    ThreadPosition(
                        thread_id=followed.thread_id, status=row["status"], cursor=cursor
                    )
                )
        drained = Drained(events=events, threads=positions, dropped=dropped, has_more=budget == 0)
        return drained, cursors

    async def _advance(
        self, owner: UUID, cursors: dict[UUID, int], dropped: Sequence[DroppedThread]
    ) -> None:
        gone = {thread.thread_id for thread in dropped}
        async with postgres.session() as session:
            for followed in self.threads:
                if followed.thread_id in gone:
                    await session.execute(
                        delete(EventQueueThread).where(EventQueueThread.id == followed.id)
                    )
                elif (cursor := cursors.get(followed.id)) is not None:
                    await session.execute(
                        update(EventQueueThread)
                        .where(EventQueueThread.id == followed.id)
                        .values(
                            after_version=func.greatest(EventQueueThread.after_version, cursor),
                            updated_by=owner,
                            updated_at=func.clock_timestamp(),
                        )
                    )
                    followed.after_version = max(followed.after_version, cursor)
            await self._extend(session, owner)
        self.threads = [t for t in self.threads if t.thread_id not in gone]

    async def _extend(self, session: AsyncSession, owner: UUID) -> None:
        cls = type(self)
        expires_at = await session.scalar(
            update(cls)
            .where(cls.id == self.id)
            .values(
                expires_at=func.clock_timestamp() + _IDLE,
                updated_by=owner,
                updated_at=func.clock_timestamp(),
            )
            .returning(cls.expires_at)
        )
        if expires_at is not None:
            self.expires_at = expires_at


def _bounded(value: JsonValue) -> JsonValue:
    if isinstance(value, str):
        return value if len(value) <= _MAX_FIELD_CHARS else value[:_MAX_FIELD_CHARS] + "…"
    if isinstance(value, (dict, list)):
        encoded = json.dumps(value)
        if len(encoded) > _MAX_FIELD_CHARS:
            return encoded[:_MAX_FIELD_CHARS] + "…"
    return value
