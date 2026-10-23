"""The shape every mirrored GitHub record shares, and how it is written."""

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import ClassVar, Self
from uuid import UUID, uuid7

from sqlalchemy import ColumnElement, ForeignKey, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.base import ReadOnlyColumnCollection

from openswe.database.orm import NOW, Base

type Excluded = ReadOnlyColumnCollection[str, ColumnElement[object]]
type Guard = Callable[[Excluded], ColumnElement[bool]]

_SERVER_SET = frozenset({"created_at", "updated_at"})


class MirrorRow(Base):
    """A GitHub record as last described to Open SWE.

    Rows are addressed by ``natural_key``, the unique index GitHub's own identity
    maps to. Only the system writes them, so ``created_by``/``updated_by`` stay NULL.
    """

    __abstract__ = True

    natural_key: ClassVar[tuple[str, ...]]

    # Set on construction, never passed: rows are only ever new descriptions to upsert.
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7, init=False)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None, init=False
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None, init=False
    )

    def values(self) -> dict[str, object]:
        return {
            column.key: getattr(self, column.key)
            for column in self.__table__.columns
            if column.key not in _SERVER_SET
        }

    @property
    def key(self) -> tuple[object, ...]:
        return tuple(getattr(self, column) for column in self.natural_key)

    @classmethod
    async def put(
        cls, session: AsyncSession, rows: Sequence[Self], *, unless_newer: Guard | None = None
    ) -> None:
        """Insert or overwrite ``rows`` by natural key; a later row with the same key wins.

        ``unless_newer`` holds a row back when the stored one says more recent things.
        """
        unique = list({row.key: row for row in rows}.values())
        if not unique:
            return
        statement = insert(cls).values([row.values() for row in unique])
        changed: dict[str, ColumnElement[object]] = {
            column.key: statement.excluded[column.key]
            for column in cls.__table__.columns
            if column.key not in _SERVER_SET | {"id", "created_by"} | set(cls.natural_key)
        }
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=list(cls.natural_key),
                set_={**changed, "updated_at": func.clock_timestamp()},
                where=unless_newer(statement.excluded) if unless_newer is not None else None,
            )
        )

    @classmethod
    async def add_missing(cls, session: AsyncSession, rows: Sequence[Self]) -> None:
        """Insert the ``rows`` not stored yet, leaving stored ones alone."""
        unique = list({row.key: row for row in rows}.values())
        if unique:
            await session.execute(
                insert(cls)
                .values([row.values() for row in unique])
                .on_conflict_do_nothing(index_elements=list(cls.natural_key))
            )
