"""The ``concierge_background`` row: a person's background thread and its standing instructions."""

from datetime import datetime
from typing import Self
from uuid import UUID, uuid7

from sqlalchemy import ForeignKey, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base


class ConciergeBackground(Base):
    __tablename__ = "concierge_background"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    thread_id: Mapped[str | None] = mapped_column(default=None)
    # The concierge DM thread whose sandbox the background thread shares.
    dm_thread_id: Mapped[str | None] = mapped_column(default=None)
    instructions: Mapped[str] = mapped_column(server_default="", default="")
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    @classmethod
    async def for_user(cls, user_id: UUID) -> Self | None:
        async with postgres.session() as session:
            return await session.scalar(select(cls).where(cls.user_id == user_id))

    @classmethod
    async def for_thread(cls, thread_id: str) -> Self | None:
        """The row whose background thread ``thread_id`` is; ``None`` for any other thread."""
        # Every agent run asks; a deployment without PostgreSQL has no background threads.
        if not postgres.configured():
            return None
        async with postgres.session() as session:
            return await session.scalar(select(cls).where(cls.thread_id == thread_id))

    @classmethod
    async def save_instructions(cls, user_id: UUID, instructions: str) -> Self:
        """Set what the person's background thread does on each check-in; the person wrote them."""
        async with postgres.session() as session:
            row = await session.scalar(
                insert(cls)
                .values(
                    id=uuid7(),
                    user_id=user_id,
                    instructions=instructions,
                    created_by=user_id,
                    updated_by=user_id,
                )
                .on_conflict_do_update(
                    index_elements=[cls.user_id],
                    set_={
                        "instructions": instructions,
                        "updated_by": user_id,
                        "updated_at": NOW,
                    },
                )
                .returning(cls)
            )
            if row is None:
                raise RuntimeError("Saving concierge instructions returned no row")
            return row

    @classmethod
    async def bind_threads(cls, user_id: UUID, thread_id: str, dm_thread_id: str) -> Self:
        """Record the person's new background thread and the DM thread it shares a sandbox with."""
        async with postgres.session() as session:
            row = await session.scalar(
                insert(cls)
                .values(id=uuid7(), user_id=user_id, thread_id=thread_id, dm_thread_id=dm_thread_id)
                .on_conflict_do_update(
                    index_elements=[cls.user_id],
                    set_={
                        "thread_id": thread_id,
                        "dm_thread_id": dm_thread_id,
                        "updated_by": None,
                        "updated_at": NOW,
                    },
                )
                .returning(cls)
            )
            if row is None:
                raise RuntimeError("Binding the concierge background thread returned no row")
            return row

    @classmethod
    async def shares_sandbox(cls, thread_id: str, host_thread_id: str) -> bool:
        """Whether ``thread_id`` is a background thread whose recorded sandbox host is that thread."""
        async with postgres.session() as session:
            found = await session.scalar(
                select(cls.id).where(cls.thread_id == thread_id, cls.dm_thread_id == host_thread_id)
            )
        return found is not None

    async def forget_thread(self) -> None:
        """Drop a background thread that is gone, so the next check-in starts a new one."""
        async with postgres.session() as session:
            await session.execute(
                update(type(self))
                .where(type(self).id == self.id, type(self).thread_id == self.thread_id)
                .values(thread_id=None, dm_thread_id=None, updated_by=None, updated_at=NOW)
            )
        self.thread_id = None
        self.dm_thread_id = None
