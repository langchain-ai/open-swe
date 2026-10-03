"""Follow-ups held for a running agent's next model call.

Each message is its own row: writers only insert and the run only deletes the
rows it consumed, so no update can overwrite another.
"""

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Self

from sqlalchemy import BigInteger, Identity, delete, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

MAX_QUEUED_MESSAGES = 100

type QueuedContent = str | list[JsonObject] | JsonObject


class QueuedMessage(Base):
    __tablename__ = "thread_queued_message"

    thread_id: Mapped[str]
    content: Mapped[QueuedContent] = mapped_column(JSONB)
    queue_id: Mapped[str | None] = mapped_column(default=None)
    seq: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True, init=False
    )
    queued_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    @classmethod
    async def put(
        cls, thread_id: str, content: QueuedContent, *, queue_id: str | None = None
    ) -> None:
        """Queue ``content``; a ``queue_id`` already queued is not queued again."""
        async with postgres.session() as session:
            await session.execute(
                insert(cls)
                .values(thread_id=thread_id, queue_id=queue_id, content=content)
                .on_conflict_do_nothing(
                    index_elements=["thread_id", "queue_id"], index_where=cls.queue_id.is_not(None)
                )
            )
            oldest_kept = (
                select(cls.seq)
                .where(cls.thread_id == thread_id)
                .order_by(cls.seq.desc())
                .offset(MAX_QUEUED_MESSAGES - 1)
                .limit(1)
                .scalar_subquery()
            )
            dropped = (
                await session.scalars(
                    delete(cls)
                    .where(cls.thread_id == thread_id, cls.seq < oldest_kept)
                    .returning(cls.seq)
                )
            ).all()
        if dropped:
            logger.warning(
                "Dropped the oldest queued messages over the cap",
                extra={"message_queue": {"thread_id": thread_id, "dropped": len(dropped)}},
            )

    @classmethod
    async def for_thread(cls, thread_id: str) -> list[Self]:
        """Every message queued for ``thread_id``, oldest first."""
        async with postgres.session() as session:
            return list(
                await session.scalars(
                    select(cls).where(cls.thread_id == thread_id).order_by(cls.seq)
                )
            )

    @classmethod
    async def remove(cls, messages: Sequence[Self]) -> None:
        if not messages:
            return
        async with postgres.session() as session:
            await session.execute(
                delete(cls).where(cls.seq.in_([message.seq for message in messages]))
            )

    @classmethod
    async def clear(cls, thread_id: str) -> None:
        async with postgres.session() as session:
            await session.execute(delete(cls).where(cls.thread_id == thread_id))
