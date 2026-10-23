"""Events owed to a thread, until a run of it says it holds them.

A matched event is written here before any run is started for it, and it counts
as delivered only once a run of the thread received it: a run it started, once
that run begins, or a running one, once the model call it read the event for goes
ahead (``received``). LangGraph's own run queue is never trusted with it: an
``interrupt`` run cancels every queued run on the thread, so whichever run comes
next delivers everything still owed, oldest first.
"""

import logging
from collections.abc import Collection, Sequence
from datetime import datetime, timedelta
from typing import Literal, Self
from uuid import UUID, uuid7

from pydantic import BaseModel, JsonValue
from sqlalchemy import Text, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.dispatch import create_durable_run, dispatch_client
from openswe.input_messages import (
    EVENT_MESSAGE_PREFIX,
    RunMessage,
    Surface,
    SystemIdentity,
    build_input_messages,
)
from openswe.threads.wake_budget import MONITOR_WAKES
from openswe.webhooks.event_log import RETAINED_DAYS, WebhookSource

logger = logging.getLogger(__name__)

type MultitaskStrategy = Literal["enqueue", "interrupt"]
# ``sandbox`` is output a monitor the thread started in its sandbox emitted.
type EventSource = WebhookSource | Literal["sandbox"]

_SYSTEM: SystemIdentity = {
    "id": "system:event-subscription",
    "display_name": "Event listener",
    "platform": "open-swe",
}
_MONITOR_SYSTEM: SystemIdentity = {
    "id": "system:monitor",
    "display_name": "Monitor",
    "platform": "open-swe",
}
_RETAINED = timedelta(days=RETAINED_DAYS)
EVENT_MATCH_KIND = "event_match"
_MAX_ATTEMPTS = 3


class _Thread(BaseModel):
    status: str = ""


class EventMatch(Base):
    __tablename__ = "event_match"

    thread_id: Mapped[str]
    source: Mapped[EventSource] = mapped_column(Text)
    delivery_id: Mapped[str]
    content: Mapped[str]
    run_config: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    subscription_id: Mapped[UUID | None] = mapped_column(default=None)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    delivery_attempts: Mapped[int] = mapped_column(server_default="0", init=False)
    matched_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    delivered_at: Mapped[datetime | None] = mapped_column(default=None, init=False)

    async def record(self, session: AsyncSession) -> bool:
        """Save in ``session``; ``False`` when the thread already owes or holds this delivery."""
        cls = type(self)
        await session.execute(delete(cls).where(cls.matched_at < func.now() - _RETAINED))
        recorded = await session.scalar(
            insert(cls)
            .values(
                id=self.id,
                thread_id=self.thread_id,
                subscription_id=self.subscription_id,
                source=self.source,
                delivery_id=self.delivery_id,
                content=self.content,
                run_config=self.run_config,
            )
            .on_conflict_do_nothing(
                index_elements=["thread_id", "source", "delivery_id"],
                # A literal: once asyncpg reuses a generic plan, a bound "" no
                # longer matches the partial index's predicate.
                index_where=text("delivery_id <> ''"),
            )
            .returning(cls.id)
        )
        return recorded is not None

    @classmethod
    async def owed(cls, thread_id: str) -> list[Self]:
        """Matches no run of the thread has received yet, oldest first."""
        async with postgres.session() as session:
            rows = await session.scalars(
                select(cls)
                .where(
                    cls.thread_id == thread_id,
                    cls.matched_at >= func.now() - _RETAINED,
                    cls.delivered_at.is_(None),
                )
                .order_by(cls.matched_at, cls.id)
            )
            return list(rows)

    @classmethod
    async def received(cls, thread_id: str, match_ids: Collection[UUID]) -> None:
        """A run of ``thread_id`` holds ``match_ids``: none is owed again."""
        if not match_ids:
            return
        async with postgres.session() as session:
            await session.execute(
                update(cls)
                .where(
                    cls.thread_id == thread_id,
                    cls.id.in_(list(match_ids)),
                    cls.delivered_at.is_(None),
                )
                .values(delivered_at=func.now())
            )

    @classmethod
    def messages(cls, matches: Sequence[Self]) -> list[RunMessage]:
        introduced: set[str] = set()
        messages: list[RunMessage] = []
        for match in matches:
            data: dict[str, str] = {"event_match": str(match.id)}
            system, surface = match.sender
            built = build_input_messages(
                match.content,
                {
                    "sender_id": system["id"],
                    "surface": surface,
                    "kind": "system",
                    "data": data,
                },
                systems=[system],
                injected_dynamic_context_hashes=introduced,
            )
            built[-1]["id"] = f"{EVENT_MESSAGE_PREFIX}{match.id}"
            messages.extend(built)
        return messages

    @property
    def sender(self) -> tuple[SystemIdentity, Surface]:
        if self.source == "sandbox":
            return _MONITOR_SYSTEM, "automation"
        return _SYSTEM, self.source

    @classmethod
    async def deliver(cls, thread_id: str, strategy: MultitaskStrategy) -> bool:
        """Start a run carrying everything the thread is owed; ``False`` when none was needed.

        ``enqueue`` leaves a busy thread alone: its next model call takes what is
        owed, and the completion webhook calls this again once it finishes. A match
        that ``_MAX_ATTEMPTS`` runs failed to deliver no longer starts one, so a
        thread whose runs keep failing is not retried forever.
        Raises ``NotFoundError`` when the thread is gone.
        """
        client = dispatch_client()
        async with postgres.transaction() as lock:
            await lock.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"{EVENT_MATCH_KIND}:{thread_id}"},
            )
            if strategy == "enqueue":
                thread = _Thread.model_validate(await client.threads.get(thread_id))
                if thread.status == "busy":
                    return False
            owed = await cls.owed(thread_id)
            if all(match.delivery_attempts >= _MAX_ATTEMPTS for match in owed):
                return False
            # Monitor output alone may start only so many runs between human
            # messages; past that it waits for a run a person starts.
            if all(match.source == "sandbox" for match in owed) and not await MONITOR_WAKES.spend(
                client, thread_id
            ):
                return False
            owed_ids = [match.id for match in owed]
            async with postgres.session() as session:
                await session.execute(
                    update(cls)
                    .where(cls.id.in_(owed_ids))
                    .values(delivery_attempts=cls.delivery_attempts + 1)
                )
            latest = owed[-1]
            turn_id = uuid7()
            await create_durable_run(
                thread_id,
                "agent",
                input={"messages": cls.messages(owed)},
                config={
                    "configurable": {
                        **latest.run_config,
                        "transcript_turn_id": str(turn_id),
                    }
                },
                metadata={
                    "kind": EVENT_MATCH_KIND,
                    "event_match_ids": [str(match_id) for match_id in owed_ids],
                },
                source=latest.source,
                thread_title=None,
                client=client,
                multitask_strategy=strategy,
                if_not_exists="reject",
            )
            return True

    @classmethod
    async def forget(cls, thread_id: str) -> None:
        async with postgres.session() as session:
            await session.execute(delete(cls).where(cls.thread_id == thread_id))
