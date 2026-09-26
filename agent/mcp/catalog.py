"""Discovered MCP tool definitions, shared by every worker so a run never repeats discovery another did."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Self

from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.utils.json_types import JsonObject
from mcp.types import Tool

CATALOG_TTL = timedelta(minutes=10)

type CatalogKey = tuple[str, str]


def catalog_key(namespace: Sequence[str], connection_name: str) -> CatalogKey:
    return json.dumps(list(namespace)), connection_name


class MCPToolCatalog(Base):
    __tablename__ = "mcp_tool_catalog"

    namespace: Mapped[str] = mapped_column(primary_key=True)
    connection_name: Mapped[str] = mapped_column(primary_key=True)
    revision: Mapped[str]
    tools: Mapped[list[JsonObject]] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)

    @property
    def stale(self) -> bool:
        return datetime.now(UTC) - self.fetched_at >= CATALOG_TTL

    def definitions(self) -> list[Tool]:
        return [Tool.model_validate(tool) for tool in self.tools]

    @classmethod
    async def load_all(cls, keys: Sequence[CatalogKey]) -> dict[CatalogKey, Self]:
        if not keys:
            return {}
        async with postgres.session() as session:
            rows = await session.scalars(
                select(cls).where(tuple_(cls.namespace, cls.connection_name).in_(keys))
            )
            return {(row.namespace, row.connection_name): row for row in rows}

    @classmethod
    async def save(cls, key: CatalogKey, revision: str, definitions: Sequence[Tool]) -> None:
        namespace, connection_name = key
        upsert = insert(cls).values(
            namespace=namespace,
            connection_name=connection_name,
            revision=revision,
            tools=[tool.model_dump(mode="json", exclude_none=True) for tool in definitions],
        )
        async with postgres.session() as session:
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[cls.namespace, cls.connection_name],
                    set_={
                        "revision": upsert.excluded.revision,
                        "tools": upsert.excluded.tools,
                        "fetched_at": func.clock_timestamp(),
                    },
                )
            )
