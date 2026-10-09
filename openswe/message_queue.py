"""Follow-ups held for a running agent's next model call.

Each message is its own row: writers only insert and the run only deletes the
rows it consumed, so no update can overwrite another.
"""

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, Identity, delete, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.ui_invalidations.topics import Topic
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

MAX_QUEUED_MESSAGES = 100

type QueuedContent = str | list[JsonObject] | JsonObject


class QueuedMessage(Base):
    __tablename__ = "thread_queued_message"

    thread_id: Mapped[str]
    content: Mapped[QueuedContent] = mapped_column(JSONB)
    # The dashboard message's own id, shared with its transcript row; unset for
    # Slack and other writers. Unique per thread, so a retried send is not queued twice.
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
            await Topic.THREAD_QUEUES.invalidate(session, key=thread_id)
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
            for thread_id in {message.thread_id for message in messages}:
                await Topic.THREAD_QUEUES.invalidate(session, key=thread_id)

    @classmethod
    async def clear(cls, thread_id: str) -> None:
        async with postgres.session() as session:
            await session.execute(delete(cls).where(cls.thread_id == thread_id))
            await Topic.THREAD_QUEUES.invalidate(session, key=thread_id)

    async def preview(self) -> QueuedPreview:
        """What a person sees of this message while it waits for the agent."""
        from openswe.incidents.report import context_message

        content = self.content
        if isinstance(content, str):
            payload = _QueuedPayload(text=content)
        elif isinstance(content, list):
            payload = _QueuedPayload(blocks=content)
        else:
            payload = _QueuedPayload.model_validate(content)
        sender = payload.sender
        return QueuedPreview(
            id=self.queue_id or f"queued-{self.seq}",
            text=context_message(payload.preview_text()),
            sender=await sender.login() if sender else None,
            platform=sender.platform if sender else None,
            queued_at=self.queued_at,
        )


class _QueuedSender(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    github_login: str | None = None
    platform: str | None = None

    async def login(self) -> str | None:
        """The sender's GitHub login; a Slack member nobody linked stays anonymous."""
        from openswe.users import User

        if self.github_login:
            return self.github_login
        platform, _, slack_user_id = (self.id or "").partition(":")
        return await User.login_for_slack(slack_user_id) if platform == "slack" else None


class _QueuedBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str = ""
    text: str | None = None


class _QueuedPayload(BaseModel):
    """The writers' payload shapes: a dashboard or Slack follow-up, or bare content blocks."""

    model_config = ConfigDict(extra="ignore")

    text: str | None = None
    content: list[_QueuedBlock] | str | None = None
    blocks: list[_QueuedBlock] = Field(default_factory=list)
    sender: _QueuedSender | None = None

    def preview_text(self) -> str:
        if self.text:
            return self.text
        if isinstance(self.content, str):
            return self.content
        blocks = self.content or self.blocks
        return "\n".join(block.text for block in blocks if block.type == "text" and block.text)


class QueuedPreview(BaseModel):
    id: str
    text: str
    sender: str | None
    platform: str | None
    queued_at: datetime | None
