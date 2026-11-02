"""Live rooms: what lets a plugin's users see each other's edits as they type.

A room is an ordered log of opaque updates (a CRDT's, for collaborative
editing) plus presence, scoped like plugin documents. Every update is a row,
notified on the shared LISTEN connection inside the transaction that wrote it,
so every replica's streams wake and read the rows themselves; a notification
that never arrives costs latency until the next one. Presence (cursors, who is
here) is never stored: it rides on the notification alone, and a member that
misses some simply hears the next.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import ColumnElement, ForeignKey, LargeBinary, delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.notifications import LISTENER
from openswe.database.orm import NOW, Base
from openswe.plugins.records import DocumentScope

logger = logging.getLogger(__name__)

CHANNEL = "plugin_room"
MAX_PRESENCE_BYTES = 4_000
"""Presence rides on a NOTIFY payload, which Postgres caps at 8000 bytes."""
_QUEUE_LIMIT = 256


class PluginRoomUpdateRow(Base):
    __tablename__ = "plugin_room_update"

    plugin: Mapped[UUID] = mapped_column(ForeignKey("plugin.id", ondelete="CASCADE"))
    room: Mapped[str]
    repository: Mapped[str | None]
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    seq: Mapped[int] = mapped_column(init=False)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )


@dataclass(frozen=True, slots=True)
class RoomUpdate:
    seq: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class RoomEvent:
    """In ``room`` (a ``Room.key``): ``update`` means the log grew; ``presence``
    carries one member's state."""

    room: str
    kind: str
    client: str = ""
    presence: str = ""


class Room:
    """One room of one plugin, in one scope."""

    def __init__(self, plugin: UUID, scope: DocumentScope, name: str) -> None:
        self.plugin = plugin
        self.scope = scope
        self.name = name

    @property
    def key(self) -> str:
        return f"{self.plugin}/{self.scope.key}#{self.name}"

    def _where(self) -> list[ColumnElement[bool]]:
        return [
            PluginRoomUpdateRow.plugin == self.plugin,
            PluginRoomUpdateRow.room == self.name,
            PluginRoomUpdateRow.repository.is_(None)
            if self.scope.repository is None
            else PluginRoomUpdateRow.repository == self.scope.repository,
            PluginRoomUpdateRow.user_id.is_(None)
            if self.scope.user_id is None
            else PluginRoomUpdateRow.user_id == self.scope.user_id,
        ]

    async def since(self, seq: int, *, limit: int = 500) -> list[RoomUpdate]:
        async with postgres.session() as session:
            rows = await session.execute(
                select(PluginRoomUpdateRow.seq, PluginRoomUpdateRow.payload)
                .where(*self._where(), PluginRoomUpdateRow.seq > seq)
                .order_by(PluginRoomUpdateRow.seq)
                .limit(limit)
            )
            return [RoomUpdate(seq, payload) for seq, payload in rows]

    async def append(self, payload: bytes, *, by: UUID) -> int:
        async with postgres.session() as session:
            seq = (
                await session.execute(
                    insert(PluginRoomUpdateRow)
                    .values(
                        id=uuid7(),
                        plugin=self.plugin,
                        room=self.name,
                        repository=self.scope.repository,
                        user_id=self.scope.user_id,
                        payload=payload,
                        created_by=by,
                        updated_by=by,
                    )
                    .returning(PluginRoomUpdateRow.seq)
                )
            ).scalar_one()
            await self._notify(session, {"kind": "update"})
            return seq

    async def compact(self, snapshot: bytes, *, through: int, by: UUID) -> int:
        """Replace the log up to ``through`` with ``snapshot``, which must cover it."""
        async with postgres.session() as session:
            await session.execute(
                delete(PluginRoomUpdateRow).where(
                    *self._where(), PluginRoomUpdateRow.seq <= through
                )
            )
            seq = (
                await session.execute(
                    insert(PluginRoomUpdateRow)
                    .values(
                        id=uuid7(),
                        plugin=self.plugin,
                        room=self.name,
                        repository=self.scope.repository,
                        user_id=self.scope.user_id,
                        payload=snapshot,
                        created_by=by,
                        updated_by=by,
                    )
                    .returning(PluginRoomUpdateRow.seq)
                )
            ).scalar_one()
            await self._notify(session, {"kind": "update"})
            return seq

    async def announce(self, client: str, presence: str) -> None:
        """Hand one member's presence to everyone in the room, storing nothing."""
        async with postgres.session() as session:
            await self._notify(
                session, {"kind": "presence", "client": client, "presence": presence}
            )

    async def _notify(self, session: AsyncSession, event: dict[str, str]) -> None:
        await session.execute(
            text("SELECT pg_notify(:channel, :payload)"),
            {"channel": CHANNEL, "payload": json.dumps({"room": self.key, **event})},
        )


_SUBSCRIBERS: dict[str, set[asyncio.Queue[RoomEvent]]] = {}


@contextlib.asynccontextmanager
async def listen(rooms: Sequence[Room]) -> AsyncIterator[asyncio.Queue[RoomEvent]]:
    """One queue of every event in ``rooms`` for the body of the ``with``, registered on entry."""
    queue: asyncio.Queue[RoomEvent] = asyncio.Queue(maxsize=_QUEUE_LIMIT)
    keys = {room.key for room in rooms}
    for key in keys:
        _SUBSCRIBERS.setdefault(key, set()).add(queue)
    try:
        yield queue
    finally:
        for key in keys:
            queues = _SUBSCRIBERS.get(key)
            if queues is not None:
                queues.discard(queue)
                if not queues:
                    _SUBSCRIBERS.pop(key, None)


def _publish(room: str, event: RoomEvent) -> None:
    """Hand ``event`` to this process's streams; a full queue drops its oldest event."""
    for queue in tuple(_SUBSCRIBERS.get(room, ())):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)


def _on_notify(payload: str) -> None:  # pragma: no cover - driven by Postgres
    try:
        event = json.loads(payload)
        room = str(event["room"])
        kind = str(event["kind"])
    except ValueError, KeyError, TypeError:
        logger.warning("Ignored an unreadable plugin room notification")
        return
    _publish(
        room,
        RoomEvent(room, kind, str(event.get("client", "")), str(event.get("presence", ""))),
    )


async def _on_connected(_down_seconds: float | None) -> None:  # pragma: no cover
    # Streams re-read the log on any update event, so one wakes them all after a gap.
    for room in tuple(_SUBSCRIBERS):
        _publish(room, RoomEvent(room, "update"))


async def start() -> None:
    if not postgres.configured():
        logger.info("Plugin rooms disabled: PostgreSQL is not configured")
        return
    await LISTENER.listen(CHANNEL, _on_notify, _on_connected)


async def stop() -> None:
    LISTENER.unlisten(CHANNEL)
    _SUBSCRIBERS.clear()
