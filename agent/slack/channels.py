"""Slack channel directory: every ``conversations.info`` payload seen, keyed by id.

Rows double as the lookup cache for channel details and as the name-to-id map;
``fresh`` bounds how long details are trusted before Slack is asked again.
"""

from datetime import UTC, datetime, timedelta
from typing import Self

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.utils.json_types import JsonObject

INFO_TTL = timedelta(seconds=300)


class SlackChannel(Base):
    __tablename__ = "slack_channel"

    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(server_default="", default="")
    info: Mapped[JsonObject] = mapped_column(JSONB, default_factory=dict)
    fetched_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    @property
    def fresh(self) -> bool:
        return self.fetched_at is not None and datetime.now(UTC) - self.fetched_at < INFO_TTL

    @classmethod
    def from_info(cls, info: JsonObject) -> Self | None:
        """A row for a Slack ``channel`` object; ``None`` when it has no id."""
        channel_id = info.get("id")
        if not isinstance(channel_id, str) or not channel_id:
            return None
        name = info.get("name")
        return cls(id=channel_id, name=name.lower() if isinstance(name, str) else "", info=info)

    @classmethod
    async def get(cls, channel_id: str) -> Self | None:
        if not postgres.configured():
            return None
        async with postgres.session() as session:
            return await session.get(cls, channel_id)

    @classmethod
    async def by_name(cls, name: str) -> Self | None:
        if not postgres.configured():
            return None
        async with postgres.session() as session:
            return await session.scalar(
                select(cls).where(cls.name == name.strip().lstrip("#").lower())
            )

    @classmethod
    async def record(cls, *infos: JsonObject) -> None:
        """Upsert what Slack just said about these channels."""
        rows = [row for row in (cls.from_info(info) for info in infos) if row is not None]
        if not rows or not postgres.configured():
            return
        upsert = insert(cls).values(
            [{"id": row.id, "name": row.name, "info": row.info} for row in rows]
        )
        async with postgres.session() as session:
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[cls.id],
                    set_={
                        "name": upsert.excluded.name,
                        "info": upsert.excluded.info,
                        "fetched_at": func.clock_timestamp(),
                    },
                )
            )
