"""Replies one agent thread owes another for a message the requester sent it."""

import logging
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Self
from uuid import UUID, uuid7

from langgraph_sdk.errors import NotFoundError
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.input_messages import Fields
from openswe.prompts import prompt
from openswe.tasks.events import run_answer
from openswe.utils.dashboard_links import dashboard_thread_url
from openswe.utils.thread_ops import langgraph_client
from openswe.webhooks.event_matches import EventMatch
from openswe.webhooks.event_subscriptions import EventSubscription

logger = logging.getLogger(__name__)
_UNANSWERED_RETAINED = timedelta(days=7)
_ANSWERING_STATUSES = frozenset({"success", "error", "timeout"})


class _Message(BaseModel):
    id: str | None = None


class _Values(BaseModel):
    messages: list[_Message] = []


class _Checkpoint(BaseModel):
    values: _Values = Field(default_factory=_Values)


class ThreadReply(Base):
    __tablename__ = "thread_reply"

    requester_thread_id: Mapped[str]
    target_thread_id: Mapped[str]
    message_id: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    requested_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    answered_at: Mapped[datetime | None] = mapped_column(default=None, init=False)

    @classmethod
    async def request(cls, requester_thread_id: str, target_thread_id: str) -> Self:
        """Owe ``requester_thread_id`` a reply to a message about to be sent to the target."""
        reply = cls(
            requester_thread_id=requester_thread_id,
            target_thread_id=target_thread_id,
            message_id=str(uuid7()),
        )
        async with postgres.session() as session:
            await session.execute(
                delete(cls).where(
                    cls.answered_at.is_(None),
                    cls.requested_at < func.now() - _UNANSWERED_RETAINED,
                )
            )
            session.add(reply)
        return reply

    def addressed(self, text: str) -> str:
        """``text`` as the target reads it: naming the thread its answer goes back to."""
        return f"{text}\n\n{Fields({'reply_to_thread': self.requester_thread_id}).text()}"

    @classmethod
    async def answer(
        cls, target_thread_id: str, run_id: str, status: str, payload: Mapping[str, object]
    ) -> None:
        """Send each requester whose message this run held the run's outcome. Never raises.

        An interrupted run leaves its replies to the run that interrupted it, which
        holds the same messages.
        """
        if status not in _ANSWERING_STATUSES or not postgres.configured():
            return
        try:
            async with postgres.session() as session:
                owed = list(
                    await session.scalars(
                        select(cls).where(
                            cls.target_thread_id == target_thread_id, cls.answered_at.is_(None)
                        )
                    )
                )
            if not owed:
                return
            history = await langgraph_client().threads.get_history(
                target_thread_id, limit=1, metadata={"run_id": run_id}
            )
            held = (
                {message.id for message in _Checkpoint.model_validate(history[0]).values.messages}
                if history
                else set()
            )
            due = [reply for reply in owed if reply.message_id in held]
            if not due:
                return
            answer = await run_answer(target_thread_id, run_id, status, payload)
        except Exception:
            logger.exception(
                "Could not answer the replies a thread owes",
                extra={"agent_thread_id": target_thread_id, "run_id": run_id},
            )
            return
        for reply in due:
            await reply.send(status, answer)

    async def send(self, status: str, answer: str) -> None:
        """Owe the requester ``answer`` as an event and start its delivery."""
        from openswe.tasks.service import recipient_config

        extra = {
            "agent_thread_id": self.target_thread_id,
            "requester_thread_id": self.requester_thread_id,
        }
        try:
            run_config = await recipient_config(self.requester_thread_id)
        except NotFoundError, PermissionError:
            logger.warning("Dropped a reply its requester can no longer take", extra=extra)
            async with postgres.session() as session:
                await self._claim(session)
            return
        except Exception:
            logger.exception("Could not reach the thread a reply is owed to", extra=extra)
            return
        try:
            async with postgres.session() as session:
                if not await self._claim(session):
                    return
                await EventMatch(
                    thread_id=self.requester_thread_id,
                    subscription_id=self.id,
                    source="thread",
                    delivery_id=f"reply:{self.id}",
                    content=prompt(
                        "threads/reply",
                        thread_id=self.target_thread_id,
                        url=dashboard_thread_url(self.target_thread_id),
                        status=status,
                        answer=answer,
                    ),
                    run_config=run_config,
                ).record(session)
        except Exception:
            logger.exception("Could not send a thread its reply", extra=extra)
            return
        await EventSubscription.deliver_to(self.requester_thread_id, "enqueue")

    async def _claim(self, session: AsyncSession) -> bool:
        cls = type(self)
        claimed = await session.scalar(
            update(cls)
            .where(cls.id == self.id, cls.answered_at.is_(None))
            .values(answered_at=func.now())
            .returning(cls.id)
        )
        return claimed is not None
