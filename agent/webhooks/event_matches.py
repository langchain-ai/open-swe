"""Events owed to a thread, delivered by being in its state.

A matched event is written here before any run is started for it, and it counts
as delivered only once a message in the thread's checkpointed state carries its
id. LangGraph's own run queue is never trusted with it: an ``interrupt`` run
cancels every queued run on the thread, so whichever run comes next delivers
everything still owed, oldest first.
"""

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal, Self
from uuid import UUID, uuid7

from pydantic import BaseModel, JsonValue
from sqlalchemy import Text, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.dispatch import create_durable_run, dispatch_client
from agent.input_messages import (
    RunMessage,
    SystemIdentity,
    build_input_messages,
    delivered_event_match_ids,
)
from agent.tasks.presentation import TaskEventMetadata
from agent.webhooks.event_log import RETAINED_DAYS, WebhookSource

logger = logging.getLogger(__name__)

type MultitaskStrategy = Literal["enqueue", "interrupt"]
type EventSource = WebhookSource | Literal["task"]

_SYSTEM: SystemIdentity = {
    "id": "system:event-subscription",
    "display_name": "Event listener",
    "platform": "open-swe",
}
_RETAINED = timedelta(days=RETAINED_DAYS)
EVENT_MATCH_KIND = "event_match"
_MAX_ATTEMPTS = 3


class _ThreadValues(BaseModel):
    messages: list[JsonValue] = []


class _ThreadState(BaseModel):
    values: _ThreadValues | None = None


class _Thread(BaseModel):
    status: str = ""


class EventMatch(Base):
    __tablename__ = "event_match"

    thread_id: Mapped[str]
    subscription_id: Mapped[UUID]
    source: Mapped[EventSource] = mapped_column(Text)
    delivery_id: Mapped[str]
    content: Mapped[str]
    run_config: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    task_event: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB, default=None)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    delivery_attempts: Mapped[int] = mapped_column(server_default="0", init=False)
    matched_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    async def record(self, session: AsyncSession) -> bool:
        """Save in ``session``; ``False`` when the thread already owes or holds this delivery."""
        cls = type(self)
        await session.execute(
            delete(cls).where(cls.source != "task", cls.matched_at < func.now() - _RETAINED)
        )
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
                task_event=self.task_event,
            )
            .on_conflict_do_nothing(
                index_elements=["thread_id", "source", "delivery_id"],
                index_where=cls.delivery_id != "",
            )
            .returning(cls.id)
        )
        return recorded is not None

    @classmethod
    async def owed(cls, thread_id: str, messages: Sequence[object]) -> list[Self]:
        """Matches no message in ``messages`` carries yet, oldest first."""
        delivered = delivered_event_match_ids(messages)
        async with postgres.session() as session:
            rows = await session.scalars(
                select(cls)
                .where(
                    cls.thread_id == thread_id,
                    (cls.source == "task") | (cls.matched_at >= func.now() - _RETAINED),
                )
                .order_by(cls.matched_at, cls.id)
            )
            return [row for row in rows if str(row.id) not in delivered]

    @classmethod
    def messages(cls, matches: Sequence[Self]) -> list[RunMessage]:
        introduced: set[str] = set()
        messages: list[RunMessage] = []
        for match in matches:
            data: dict[str, object] = {"event_match": str(match.id)}
            if match.source == "task" and match.task_event is not None:
                data["task_event"] = TaskEventMetadata.model_validate(
                    match.task_event
                ).model_dump_json()
            built = build_input_messages(
                match.content,
                {
                    "sender_id": _SYSTEM["id"],
                    "surface": "automation" if match.source == "task" else match.source,
                    "kind": "system",
                    "data": data,
                },
                systems=[_SYSTEM],
                injected_dynamic_context_hashes=introduced,
            )
            built[-1]["id"] = f"event-match:{match.id}"
            messages.extend(built)
        return messages

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
            state = _ThreadState.model_validate(await client.threads.get_state(thread_id))
            owed = await cls.owed(thread_id, state.values.messages if state.values else [])
            if all(match.delivery_attempts >= _MAX_ATTEMPTS for match in owed):
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
