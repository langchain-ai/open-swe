"""Per-thread documents in PostgreSQL: one JSON document per thread and kind.

A document outlives its thread's retention, so it keys on the thread id
without a foreign key.
"""

import logging
from datetime import datetime
from uuid import UUID, uuid7

from pydantic import BaseModel, ValidationError
from sqlalchemy import ColumnElement, ForeignKey, ScalarSelect, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.users.records import user_id_for_login
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)


class ThreadDocument(Base):
    __tablename__ = "thread_document"

    thread_id: Mapped[str]
    kind: Mapped[str]
    document: Mapped[JsonObject] = mapped_column(JSONB)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )


def _person(login: str | None) -> ScalarSelect[UUID] | None:
    """The ``users.id`` of the person with GitHub ``login``; ``None`` for a system write."""
    return user_id_for_login(login) if login else None


class ThreadDocuments[DocumentT: BaseModel]:
    """Every thread's document of one ``kind``, validated against ``model``."""

    def __init__(self, kind: str, model: type[DocumentT]) -> None:
        self.kind = kind
        self.model = model

    async def get(self, thread_id: str) -> DocumentT | None:
        async with postgres.session() as session:
            value = await session.scalar(
                select(ThreadDocument.document).where(
                    ThreadDocument.thread_id == thread_id, ThreadDocument.kind == self.kind
                )
            )
        return None if value is None else self.model.model_validate(value)

    async def put(self, thread_id: str, document: DocumentT, *, by: str | None = None) -> None:
        """Save ``thread_id``'s document; ``by`` is the GitHub login of the person who caused it."""
        value = document.model_dump(mode="json")
        person = _person(by)
        async with postgres.session() as session:
            await session.execute(
                insert(ThreadDocument)
                .values(
                    id=uuid7(),
                    thread_id=thread_id,
                    kind=self.kind,
                    document=value,
                    created_by=person,
                    updated_by=person,
                )
                .on_conflict_do_update(
                    index_elements=[ThreadDocument.thread_id, ThreadDocument.kind],
                    set_={
                        "document": value,
                        "updated_at": func.clock_timestamp(),
                        "updated_by": person,
                    },
                )
            )

    async def delete(self, thread_id: str) -> None:
        async with postgres.session() as session:
            await session.execute(
                delete(ThreadDocument).where(
                    ThreadDocument.thread_id == thread_id, ThreadDocument.kind == self.kind
                )
            )

    async def where(self, field: str, value: str) -> list[tuple[str, DocumentT]]:
        """Each ``(thread_id, document)`` whose document has ``field`` equal to ``value``."""
        return await self._select(ThreadDocument.document[field].astext == value)

    async def all(self) -> list[tuple[str, DocumentT]]:
        """Every ``(thread_id, document)`` of this kind, skipping unreadable ones."""
        return await self._select(None)

    async def written_at(self, thread_id: str) -> datetime | None:
        async with postgres.session() as session:
            return await session.scalar(
                select(ThreadDocument.updated_at).where(
                    ThreadDocument.thread_id == thread_id, ThreadDocument.kind == self.kind
                )
            )

    async def _select(self, condition: ColumnElement[bool] | None) -> list[tuple[str, DocumentT]]:
        query = select(ThreadDocument.thread_id, ThreadDocument.document).where(
            ThreadDocument.kind == self.kind
        )
        if condition is not None:
            query = query.where(condition)
        async with postgres.session() as session:
            rows = (await session.execute(query)).tuples().all()
        documents: list[tuple[str, DocumentT]] = []
        for thread_id, value in rows:
            try:
                documents.append((thread_id, self.model.model_validate(value)))
            except ValidationError:
                logger.warning(
                    "Skipping unreadable thread document",
                    extra={"document_kind": self.kind, "agent_thread_id": thread_id},
                    exc_info=True,
                )
        return documents
