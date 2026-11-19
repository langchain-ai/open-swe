"""PostgreSQL records for global settings and execution profiles."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import ColumnElement, and_, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.utils.json_types import JsonObject


class SettingsRecord(Base):
    __tablename__ = "settings_record"

    tier: Mapped[str] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[JsonObject] = mapped_column(JSONB)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


class SettingsRecords:
    """Every settings record of one tier."""

    def __init__(self, tier: Literal["instance", "execution"]) -> None:
        self.tier = tier

    def _where(self, key: str) -> ColumnElement[bool]:
        return and_(SettingsRecord.tier == self.tier, SettingsRecord.key == key)

    async def get(self, key: str = "", *, session: AsyncSession | None = None) -> JsonObject | None:
        async with postgres.session(session) as session:
            return await session.scalar(select(SettingsRecord.value).where(self._where(key)))

    async def for_keys(self, keys: list[str], *, session: AsyncSession) -> dict[str, JsonObject]:
        if not keys:
            return {}
        rows = await session.execute(
            select(SettingsRecord.key, SettingsRecord.value).where(
                SettingsRecord.tier == self.tier, SettingsRecord.key.in_(keys)
            )
        )
        return dict(rows.tuples().all())

    async def put(
        self, key: str, value: JsonObject, *, session: AsyncSession | None = None
    ) -> None:
        async with postgres.session(session) as session:
            upsert = insert(SettingsRecord).values(tier=self.tier, key=key, value=value)
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[SettingsRecord.tier, SettingsRecord.key],
                    set_={"value": value, "updated_at": func.clock_timestamp()},
                )
            )

    async def lock(self, key: str = "", *, session: AsyncSession) -> JsonObject:
        """Read a record under the caller's transaction lock, creating an empty one if absent."""
        await session.execute(
            insert(SettingsRecord)
            .values(tier=self.tier, key=key, value={})
            .on_conflict_do_nothing(index_elements=[SettingsRecord.tier, SettingsRecord.key])
        )
        return (
            await session.scalars(
                select(SettingsRecord.value).where(self._where(key)).with_for_update()
            )
        ).one()

    async def patch(
        self, key: str, value: JsonObject, *, session: AsyncSession | None = None
    ) -> tuple[JsonObject, JsonObject]:
        async with postgres.session(session) as session:
            before = await self.lock(key, session=session)
            upsert = insert(SettingsRecord).values(tier=self.tier, key=key, value=value)
            saved = await session.scalar(
                upsert.on_conflict_do_update(
                    index_elements=[SettingsRecord.tier, SettingsRecord.key],
                    set_={
                        "value": SettingsRecord.value.op("||")(upsert.excluded.value),
                        "updated_at": func.clock_timestamp(),
                    },
                ).returning(SettingsRecord.value)
            )
            if saved is None:
                raise RuntimeError("Settings update did not return a record")
            return before, saved

    async def delete(self, key: str) -> None:
        async with postgres.session() as session:
            await session.execute(delete(SettingsRecord).where(self._where(key)))

    @staticmethod
    async def pinned_releases(session: AsyncSession) -> frozenset[UUID]:
        """Every app release pinned by a current or historical record, retained for cleanup."""
        found: set[UUID] = set()
        for value in await session.scalars(select(SettingsRecord.value)):
            for field in ("review_apps", "featured_apps"):
                entries = value.get(field) if isinstance(value, dict) else None
                for entry in entries if isinstance(entries, list) else []:
                    try:
                        found.add(UUID(str(entry["release"])))
                    except KeyError, TypeError, ValueError:
                        continue
        return frozenset(found)


INSTANCE_SETTINGS = SettingsRecords("instance")
