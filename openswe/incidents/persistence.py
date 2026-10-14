"""PostgreSQL persistence for incident records and their curated documents."""

import logging
from typing import Literal

from pydantic import BaseModel, ValidationError
from sqlalchemy import Text, delete, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import Base
from openswe.ui_invalidations.topics import KeyedTopic, Topic

logger = logging.getLogger(__name__)
type RecordKind = Literal["policies", "incidents", "reports", "commands", "history", "summaries"]


class IncidentRow(Base):
    __tablename__ = "incident_record"

    kind: Mapped[RecordKind] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(primary_key=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)


class IncidentRepository[RecordT: BaseModel, KeyT: str = str]:
    """Validated records with topic invalidations after committed writes."""

    def __init__(
        self,
        kind: RecordKind,
        model: type[RecordT],
        *,
        invalidates: KeyedTopic[KeyT] | Topic | None = None,
    ) -> None:
        self.kind = kind
        self.model = model
        self.invalidates = invalidates

    async def get(self, key: KeyT) -> RecordT | None:
        async with postgres.session() as session:
            row = await session.get(IncidentRow, (self.kind, key))
            return None if row is None else self.model.model_validate(row.payload)

    async def put(self, key: KeyT, record: RecordT) -> RecordT:
        statement = insert(IncidentRow).values(
            kind=self.kind, key=key, payload=record.model_dump(mode="json")
        )
        async with postgres.transaction() as conn:
            await conn.execute(
                statement.on_conflict_do_update(
                    index_elements=[IncidentRow.kind, IncidentRow.key],
                    set_={"payload": statement.excluded.payload},
                )
            )
        await self._invalidate(key)
        return record

    async def delete(self, key: KeyT) -> None:
        async with postgres.transaction() as conn:
            await conn.execute(
                delete(IncidentRow).where(IncidentRow.kind == self.kind, IncidentRow.key == key)
            )
        await self._invalidate(key)

    async def _invalidate(self, key: KeyT) -> None:
        match self.invalidates:
            case KeyedTopic():
                await self.invalidates.invalidate(key=key)
            case Topic():
                await self.invalidates.invalidate()

    async def search(
        self, *, filter: dict[str, object] | None = None, limit: int = 100, offset: int = 0
    ) -> list[RecordT]:
        return await self._search(filter, limit, offset)

    async def search_all(
        self, *, filter: dict[str, object] | None = None, page_size: int = 100
    ) -> list[RecordT]:
        return await self._search(filter, None, 0)

    async def _search(
        self, filter: dict[str, object] | None, limit: int | None, offset: int
    ) -> list[RecordT]:
        statement = select(IncidentRow).where(IncidentRow.kind == self.kind)
        if filter:
            statement = statement.where(IncidentRow.payload.contains(filter))
        statement = statement.order_by(IncidentRow.key).offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        async with postgres.session() as session:
            rows = await session.scalars(statement)
            records = []
            for row in rows:
                try:
                    records.append(self.model.model_validate(row.payload))
                except ValidationError:
                    logger.warning(
                        "Skipping unreadable incident record",
                        extra={"record_kind": self.kind, "record_key": row.key},
                        exc_info=True,
                    )
            return records


class IncidentSummary(BaseModel):
    markdown: str


SUMMARIES = IncidentRepository[IncidentSummary]("summaries", IncidentSummary)


async def import_legacy(kind: RecordKind, key: str, payload: dict[str, object]) -> bool:
    """Copy one legacy record verbatim without replacing a PostgreSQL write."""
    async with postgres.transaction() as conn:
        result = await conn.execute(
            insert(IncidentRow)
            .values(kind=kind, key=key, payload=payload)
            .on_conflict_do_nothing(index_elements=[IncidentRow.kind, IncidentRow.key])
            .returning(IncidentRow.key)
        )
        return result.scalar_one_or_none() is not None
