"""Durable task messages, retained until checkpointed by their recipient."""

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal, Self
from uuid import NAMESPACE_URL, UUID, uuid5, uuid7

from pydantic import BaseModel, JsonValue, ValidationError
from sqlalchemy import ForeignKey, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.dispatch import create_durable_run, dispatch_client
from openswe.input_messages import (
    RunMessage,
    SystemIdentity,
    VisibleContext,
    build_input_messages,
    delivered_event_match_ids,
)
from openswe.tasks.presentation import TaskEventMetadata
from openswe.tasks.store import TaskDelegation

logger = logging.getLogger(__name__)
TASK_MESSAGE_KIND = "task_message"
_RETAINED = timedelta(days=7)
_MAX_ATTEMPTS = 3
_SYSTEM: SystemIdentity = {
    "id": "system:event-subscription",
    "display_name": "Task coordination",
    "platform": "open-swe",
}


class _ThreadValues(BaseModel):
    messages: list[JsonValue] = []


class _ThreadState(BaseModel):
    values: _ThreadValues | None = None


class _Thread(BaseModel):
    status: str = ""


class TaskMessage(Base):
    __tablename__ = "task_message"

    task_id: Mapped[UUID] = mapped_column(ForeignKey("task.id", ondelete="CASCADE"))
    thread_id: Mapped[str]
    delivery_id: Mapped[str]
    content: Mapped[str]
    run_config: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    task_event: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB, default=None)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    delivery_attempts: Mapped[int] = mapped_column(server_default="0", init=False)
    matched_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    delivered_at: Mapped[datetime | None] = mapped_column(default=None, init=False)

    async def record(self, session: AsyncSession) -> bool:
        cls = type(self)
        self.id = uuid5(
            NAMESPACE_URL, f"task-message:{self.task_id}:{self.thread_id}:{self.delivery_id}"
        )
        await session.execute(delete(cls).where(cls.delivered_at < func.now() - _RETAINED))
        recorded = await session.scalar(
            insert(cls)
            .values(
                id=self.id,
                task_id=self.task_id,
                thread_id=self.thread_id,
                delivery_id=self.delivery_id,
                content=self.content,
                run_config=self.run_config,
                task_event=self.task_event,
            )
            .on_conflict_do_nothing(index_elements=["thread_id", "delivery_id"])
            .returning(cls.id)
        )
        return recorded is not None

    @classmethod
    async def owed(cls, thread_id: str, messages: Sequence[object]) -> list[Self]:
        delivered: list[UUID] = []
        for message_id in delivered_event_match_ids(messages):
            try:
                delivered.append(UUID(message_id))
            except ValueError:
                logger.warning(
                    "Ignoring a malformed delivered task-message id",
                    extra={"agent_thread_id": thread_id, "message_id": message_id},
                )
        async with postgres.session() as session:
            await session.execute(
                update(cls)
                .where(
                    cls.thread_id == thread_id, cls.id.in_(delivered), cls.delivered_at.is_(None)
                )
                .values(delivered_at=func.now())
            )
            await session.execute(delete(cls).where(cls.delivered_at < func.now() - _RETAINED))
            return list(
                await session.scalars(
                    select(cls)
                    .where(cls.thread_id == thread_id, cls.delivered_at.is_(None))
                    .order_by(cls.matched_at, cls.id)
                )
            )

    @classmethod
    def messages(cls, matches: Sequence[Self]) -> list[RunMessage]:
        visible = VisibleContext()
        messages: list[RunMessage] = []
        for match in matches:
            data: dict[str, object] = {"event_match": str(match.id)}
            if match.task_event is not None:
                try:
                    data["task_event"] = TaskEventMetadata.model_validate(
                        match.task_event
                    ).model_dump_json()
                except ValidationError:
                    logger.warning(
                        "Delivering a task message without unreadable display metadata",
                        exc_info=True,
                        extra={"message_id": str(match.id), "agent_thread_id": match.thread_id},
                    )
            built = build_input_messages(
                match.content,
                {
                    "sender_id": _SYSTEM["id"],
                    "surface": "automation",
                    "kind": "system",
                    "data": data,
                },
                systems=[_SYSTEM],
                visible=visible,
            )
            built[-1]["id"] = f"event-match:{match.id}"
            messages.extend(built)
        return messages

    @classmethod
    async def deliver_to(cls, thread_id: str) -> None:
        if not postgres.configured():
            return
        try:
            await cls.deliver(thread_id, "enqueue")
        except Exception:
            logger.warning(
                "Could not deliver pending task messages",
                extra={"agent_thread_id": thread_id},
                exc_info=True,
            )

    @classmethod
    async def deliver(cls, thread_id: str, strategy: Literal["enqueue", "interrupt"]) -> bool:
        async with postgres.session() as session:
            pending = await session.scalar(
                select(cls.id)
                .where(cls.thread_id == thread_id, cls.delivered_at.is_(None))
                .limit(1)
            )
        if pending is None:
            return False
        client = dispatch_client()
        async with postgres.transaction() as lock:
            await lock.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"event_match:{thread_id}"},
            )
            delegation = await TaskDelegation.get(thread_id)
            if delegation is not None and delegation.cancelled:
                return False
            if strategy == "enqueue":
                thread = _Thread.model_validate(await client.threads.get(thread_id))
                if thread.status == "busy":
                    return False
            state = _ThreadState.model_validate(await client.threads.get_state(thread_id))
            owed = await cls.owed(thread_id, state.values.messages if state.values else [])
            if all(message.delivery_attempts >= _MAX_ATTEMPTS for message in owed):
                return False
            owed_ids = [message.id for message in owed]
            async with postgres.session() as session:
                await session.execute(
                    update(cls)
                    .where(cls.id.in_(owed_ids))
                    .values(delivery_attempts=cls.delivery_attempts + 1)
                )
            latest = owed[-1]
            await create_durable_run(
                thread_id,
                "agent",
                input={"messages": cls.messages(owed)},
                config={"configurable": {**latest.run_config, "transcript_turn_id": str(uuid7())}},
                metadata={
                    "kind": TASK_MESSAGE_KIND,
                    "event_match_ids": [str(message_id) for message_id in owed_ids],
                },
                source="task",
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
