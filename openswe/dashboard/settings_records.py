"""Instance and workspace settings records in PostgreSQL: the instance's under ``""``, a workspace's under its slug."""

from datetime import datetime
from typing import Literal

from sqlalchemy import ColumnElement, and_, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert
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

    def __init__(self, tier: Literal["instance", "workspace"]) -> None:
        self.tier = tier

    def _where(self, key: str) -> ColumnElement[bool]:
        return and_(SettingsRecord.tier == self.tier, SettingsRecord.key == key)

    async def get(self, key: str = "") -> JsonObject | None:
        async with postgres.session() as session:
            return await session.scalar(select(SettingsRecord.value).where(self._where(key)))

    async def put(self, key: str, value: JsonObject) -> None:
        async with postgres.session() as session:
            upsert = insert(SettingsRecord).values(tier=self.tier, key=key, value=value)
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[SettingsRecord.tier, SettingsRecord.key],
                    set_={"value": value, "updated_at": func.clock_timestamp()},
                )
            )

    async def delete(self, key: str) -> None:
        async with postgres.session() as session:
            await session.execute(delete(SettingsRecord).where(self._where(key)))


INSTANCE_SETTINGS = SettingsRecords("instance")
WORKSPACE_SETTINGS = SettingsRecords("workspace")
