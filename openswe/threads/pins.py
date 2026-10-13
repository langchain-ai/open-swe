"""The threads each person pinned to the top of their dashboard sidebar."""

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.database.store_imports import StoreImport
from openswe.store import delete_value, search_all_entries
from openswe.users.records import UnknownUser, user_id_for_login

logger = logging.getLogger(__name__)

LEGACY_THREAD_PINS_NAMESPACE = "thread_pins"


class ThreadPin(Base):
    __tablename__ = "thread_pin"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    thread_id: Mapped[str] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)


async def list_thread_pin_ids(login: str) -> list[str]:
    """The person's pinned thread IDs, oldest pin first."""
    async with postgres.session() as session:
        pins = await session.scalars(
            select(ThreadPin.thread_id)
            .where(ThreadPin.user_id == user_id_for_login(login))
            .order_by(ThreadPin.created_at, ThreadPin.thread_id)
        )
        return list(pins)


async def pin_thread(login: str, thread_id: str) -> None:
    async with postgres.session() as session:
        user_id = await session.scalar(select(user_id_for_login(login)))
        if user_id is None:
            raise UnknownUser(login)
        await session.execute(
            insert(ThreadPin).values(user_id=user_id, thread_id=thread_id).on_conflict_do_nothing()
        )


async def unpin_thread(login: str, thread_id: str) -> None:
    async with postgres.session() as session:
        await session.execute(
            delete(ThreadPin).where(
                ThreadPin.user_id == user_id_for_login(login), ThreadPin.thread_id == thread_id
            )
        )


async def import_store_pins() -> StoreImport:
    """Move pins, keyed by login in the LangGraph Store, into PostgreSQL.

    A pin whose login has no ``users`` row stays in the Store for the next startup.
    """
    moved = waiting = 0
    for entry in await search_all_entries([LEGACY_THREAD_PINS_NAMESPACE]):
        namespace = entry.namespace
        if namespace is None or len(namespace) != 2:
            logger.warning(
                "Skipping unexpected thread pin namespace", extra={"namespace": namespace}
            )
            continue
        thread_id = entry.value.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            try:
                await pin_thread(namespace[1], thread_id)
            except UnknownUser:
                logger.warning(
                    "Thread pin waits for its users row", extra={"github_login": namespace[1]}
                )
                waiting += 1
                continue
        await delete_value(namespace, entry.key)
        moved += 1
    return StoreImport(moved, waiting)
