"""Python code listening for event log rows about a pull request, until it expires.

Agent threads listen through ``EventSubscription``; code listens here. A feature
subclasses ``EventHandler`` with a ``name``, the pydantic model of what each of its
listeners keeps, and what to do when its pull request changes or the listener
expires. Every feature's listeners share the one ``event_listener`` table.

Webhooks come in bursts (a push sets off every check), so a handler hears a change
once per burst: a pull request's event queues one job per handler, ``delay`` later,
which hands the handler every listener it has on that pull request.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Final, Self
from uuid import UUID, uuid7

from pydantic import BaseModel, JsonValue
from sqlalchemy import ColumnElement, ForeignKey, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.pull_requests import PullRequest
from openswe.jobs import queue
from openswe.webhooks.event_log import LoggedEvent

logger = logging.getLogger(__name__)

HEAR_JOB: Final = "event_listener_hear"
EXPIRE_JOB: Final = "event_listener_expire"


class EventHandler:
    """What a feature does for its listeners; subclasses register themselves by ``name``."""

    name: ClassVar[str]
    state: ClassVar[type[BaseModel]]
    delay: ClassVar[timedelta] = timedelta(0)
    _handlers: ClassVar[dict[str, EventHandler]] = {}

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        EventHandler._handlers[cls.name] = cls()

    @classmethod
    def named(cls, name: str) -> EventHandler | None:
        return cls._handlers.get(name)

    async def heard(self, listeners: Sequence[EventListener]) -> None:
        """The pull request these live listeners follow changed; raise to be retried."""
        raise NotImplementedError

    async def expired(self, listener: EventListener) -> None:
        """``listener`` ran out; it is deleted after this returns."""


class EventListener(Base):
    """One thing a handler keeps following on a pull request until ``expires_at``."""

    __tablename__ = "event_listener"

    handler: Mapped[str]
    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey("pull_request.id", ondelete="CASCADE"))
    handler_state: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    expires_at: Mapped[datetime]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    pull_request: Mapped[PullRequest] = relationship(init=False, lazy="joined")

    @classmethod
    async def start(
        cls,
        handler: type[EventHandler],
        state: BaseModel,
        pull_request_id: UUID,
        *,
        expires_at: datetime,
        by: UUID | None,
        listener_id: UUID | None = None,
    ) -> Self:
        """Follow the pull request for ``handler`` until ``expires_at``."""
        listener = cls(
            id=listener_id or uuid7(),
            handler=handler.name,
            pull_request_id=pull_request_id,
            handler_state=state.model_dump(mode="json"),
            expires_at=expires_at,
            created_by=by,
            updated_by=by,
        )
        async with postgres.session() as session:
            session.add(listener)
        await queue.schedule(
            EXPIRE_JOB,
            {"listener_id": str(listener.id)},
            due_at=expires_at,
            key=f"{EXPIRE_JOB}:{listener.id}",
        )
        return listener

    def state_as[S: BaseModel](self, model: type[S]) -> S:
        return model.model_validate(self.handler_state)

    @property
    def live(self) -> bool:
        return self.expires_at > datetime.now(UTC)

    @classmethod
    def _live(cls) -> ColumnElement[bool]:
        return cls.expires_at > func.clock_timestamp()

    @classmethod
    async def deliver(cls, event: LoggedEvent) -> None:
        """Queue each handler following ``event``'s pull request to hear it. Never raises."""
        if event.pull_request_id is None:
            return
        try:
            async with postgres.session() as session:
                handlers = list(
                    await session.scalars(
                        select(cls.handler)
                        .where(cls.pull_request_id == event.pull_request_id, cls._live())
                        .distinct()
                    )
                )
            for name in handlers:
                await cls._queue_hearing(name, event.pull_request_id)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Queueing event listeners failed",
                extra={"event_delivery_id": event.delivery_id},
                exc_info=True,
            )

    @classmethod
    async def _queue_hearing(cls, name: str, pull_request_id: UUID) -> None:
        handler = EventHandler.named(name)
        if handler is None:
            logger.warning("Event listener has no handler", extra={"event_handler": name})
            return
        key = f"{HEAR_JOB}:{name}:{pull_request_id}"
        # A hearing still waiting reads this change too; one already running may not.
        waiting = await queue.get(key)
        if waiting is not None and waiting.claim_token is None:
            return
        await queue.schedule(
            HEAR_JOB,
            {"handler": name, "pull_request_id": str(pull_request_id)},
            due_at=datetime.now(UTC) + handler.delay,
            key=key,
        )

    @classmethod
    async def hear(cls, name: str, pull_request_id: UUID) -> int:
        """Hand ``name``'s live listeners on the pull request to it; how many there were."""
        handler = EventHandler.named(name)
        if handler is None:
            return 0
        async with postgres.session() as session:
            listeners = list(
                await session.scalars(
                    select(cls).where(
                        cls.handler == name,
                        cls.pull_request_id == pull_request_id,
                        cls._live(),
                    )
                )
            )
        if listeners:
            await handler.heard(listeners)
        return len(listeners)

    @classmethod
    async def expire(cls, listener_id: UUID) -> None:
        async with postgres.session() as session:
            listener = await session.get(cls, listener_id)
        if listener is None:
            return
        handler = EventHandler.named(listener.handler)
        if handler is not None:
            await handler.expired(listener)
        await listener.forget()

    async def forget(self) -> None:
        """Stop following, e.g. once what the listener updates is gone."""
        async with postgres.session() as session:
            await session.execute(delete(type(self)).where(type(self).id == self.id))
        await queue.cancel_key(f"{EXPIRE_JOB}:{self.id}")
