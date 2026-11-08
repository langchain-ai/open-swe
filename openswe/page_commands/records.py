"""The commands Robin queued for each page session, in the order the page carries them out."""

import asyncio
from datetime import datetime
from uuid import UUID, uuid7

from fastapi import HTTPException
from pydantic import BaseModel, JsonValue
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import ColumnElement

from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.oauth import session_user_id
from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.ui_invalidations import Topic
from openswe.ui_invalidations.hub import HUB
from openswe.users import User
from openswe.utils.json_types import JsonObject

# How many results one read of a queue's progress returns at most.
MAX_PROGRESS_RESULTS = 20
# How long a read of a queue's progress waits at most for the page to reach a command.
MAX_WAIT_SECONDS = 20.0


class PageCommandRow(Base):
    __tablename__ = "page_command"

    owner: Mapped[UUID] = mapped_column()
    page_session: Mapped[UUID] = mapped_column()
    position: Mapped[int]
    thread_id: Mapped[str]
    tool: Mapped[str]
    arguments: Mapped[JsonObject] = mapped_column(JSONB)
    executed_at: Mapped[datetime | None] = mapped_column(default=None)
    result: Mapped[JsonValue] = mapped_column(JSONB, default=None)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(default=None)
    updated_by: Mapped[UUID | None] = mapped_column(default=None)


class PageCommand(BaseModel):
    """A command waiting for the page, under the page's own name for its tool."""

    position: int
    tool: str
    arguments: JsonObject


class PageCommandResult(BaseModel):
    position: int
    tool: str
    result: JsonValue


class PageQueueProgress(BaseModel):
    """How far the page has carried out its queue."""

    # The last command the reading thread queued, 0 for none.
    queued_through: int
    # The last command the page carried out with every one before it, from any thread,
    # 0 for none.
    executed_through: int
    # What the reading thread's commands after the read's ``since`` returned, in order.
    results: list[PageCommandResult]


class PageCommandOutOfTurn(LookupError):
    def __init__(self, position: int) -> None:
        super().__init__(f"command {position} is not the next one waiting")


class PageQueue:
    """One page session's commands, as the person whose page it is sees them."""

    def __init__(self, page_session: UUID, owner: UUID) -> None:
        self.page_session = page_session
        self.owner = owner

    @classmethod
    async def of_request(
        cls, page_session: UUID, session: dict[str, object] = SESSION_DEP
    ) -> PageQueue:
        """The signed-in person's queue for one of their page sessions."""
        login = session.get("sub")
        user = await User.for_session(
            session_user_id(session), login if isinstance(login, str) else ""
        )
        if user is None:
            raise HTTPException(403, "page commands need an Open SWE account")
        return cls(page_session, user.id)

    @property
    def topic(self) -> str:
        """The invalidation topic the page hears its queue change on."""
        return Topic.PAGE_COMMANDS.keyed(self._topic_key)

    @property
    def _topic_key(self) -> str:
        return f"{self.owner}/{self.page_session}"

    def _mine(self) -> ColumnElement[bool]:
        return (PageCommandRow.owner == self.owner) & (
            PageCommandRow.page_session == self.page_session
        )

    async def enqueue(self, thread_id: str, tool: str, arguments: JsonObject) -> int:
        """Queue a command after every other one; its position, counting from 1."""
        async with postgres.session() as session:
            # Calls a run makes in parallel each take the next position in turn.
            await session.execute(
                select(func.pg_advisory_xact_lock(func.hashtextextended(self._topic_key, 0)))
            )
            last = await session.scalar(
                select(func.max(PageCommandRow.position)).where(self._mine())
            )
            position = (last or 0) + 1
            session.add(
                PageCommandRow(
                    owner=self.owner,
                    page_session=self.page_session,
                    position=position,
                    thread_id=thread_id,
                    tool=tool,
                    arguments=arguments,
                    # The owner's message is what had Robin queue it.
                    created_by=self.owner,
                )
            )
            await Topic.PAGE_COMMANDS.invalidate(session, key=self._topic_key)
        return position

    async def waiting(self) -> list[PageCommand]:
        """The commands the page has yet to carry out, in order."""
        async with postgres.session() as session:
            rows = await session.scalars(
                select(PageCommandRow)
                .where(self._mine(), PageCommandRow.executed_at.is_(None))
                .order_by(PageCommandRow.position)
            )
            return [
                PageCommand(position=r.position, tool=r.tool, arguments=r.arguments) for r in rows
            ]

    async def record(self, position: int, result: JsonValue, *, by: UUID) -> None:
        """Keep what the page's command at ``position`` returned; it must be the next one waiting."""
        async with postgres.session() as session:
            earlier_waiting = (
                select(PageCommandRow.id)
                .where(
                    self._mine(),
                    PageCommandRow.executed_at.is_(None),
                    PageCommandRow.position < position,
                )
                .exists()
            )
            recorded = await session.scalar(
                update(PageCommandRow)
                .where(
                    self._mine(),
                    PageCommandRow.position == position,
                    PageCommandRow.executed_at.is_(None),
                    ~earlier_waiting,
                )
                .values(
                    executed_at=func.clock_timestamp(),
                    result=result,
                    updated_at=func.clock_timestamp(),
                    updated_by=by,
                )
                .returning(PageCommandRow.id)
            )
            if recorded is None:
                raise PageCommandOutOfTurn(position)
            await Topic.PAGE_COMMANDS.invalidate(session, key=self._topic_key)

    async def progress(
        self, thread_id: str, since: int = 0, *, wait_for: int | None = None
    ) -> PageQueueProgress:
        """How far the page has got, with what ``thread_id``'s commands after ``since`` returned.

        With ``wait_for``, waits up to ``MAX_WAIT_SECONDS`` for the page to carry out the
        command at that position, waking on the queue's invalidations rather than polling.
        """
        if wait_for is None:
            return await self._progress(thread_id, since)
        deadline = asyncio.get_running_loop().time() + MAX_WAIT_SECONDS
        # Subscribed before the first read, so a result recorded between the read and
        # the wait still wakes it.
        with HUB.subscribe(frozenset({self.topic})) as changes:
            while True:
                progress = await self._progress(thread_id, since)
                remaining = deadline - asyncio.get_running_loop().time()
                if progress.executed_through >= wait_for or remaining <= 0:
                    return progress
                await changes.drain(remaining)

    async def _progress(self, thread_id: str, since: int) -> PageQueueProgress:
        ours = PageCommandRow.thread_id == thread_id
        async with postgres.session() as session:
            queued, last, first_waiting = (
                await session.execute(
                    select(
                        func.max(PageCommandRow.position).filter(ours),
                        func.max(PageCommandRow.position),
                        func.min(PageCommandRow.position).filter(
                            PageCommandRow.executed_at.is_(None)
                        ),
                    ).where(self._mine())
                )
            ).one()
            executed = await session.execute(
                select(PageCommandRow.position, PageCommandRow.tool, PageCommandRow.result)
                .where(
                    self._mine(),
                    ours,
                    PageCommandRow.executed_at.is_not(None),
                    PageCommandRow.position > since,
                )
                .order_by(PageCommandRow.position)
                .limit(MAX_PROGRESS_RESULTS)
            )
            results = [
                PageCommandResult(position=position, tool=tool, result=result)
                for position, tool, result in executed
            ]
        return PageQueueProgress(
            queued_through=queued or 0,
            # The page carries out every thread's commands in one order.
            executed_through=(last or 0) if first_waiting is None else first_waiting - 1,
            results=results,
        )
