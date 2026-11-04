"""Each tier's MCP connections in PostgreSQL, by name.

Each workspace's live in ``mcp_connection``, and a person's in ``user_record``. A tier's ``namespace`` keys its
discovery and OAuth token caches.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime
from uuid import UUID, uuid7

from pydantic import ValidationError
from sqlalchemy import ColumnElement, and_, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.mcp.models import MCPConnection, MCPScope
from openswe.mcp.runtime import MCPSource
from openswe.users.records import UserRecords
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

WORKSPACE_MCPS_NAMESPACE = ("workspace_mcps",)
USER_MCPS_NAMESPACE = ("user_mcps",)
_PERSONAL_KIND = "mcp_connection"


class MCPConnectionRow(Base):
    __tablename__ = "mcp_connection"

    workspace: Mapped[str | None]
    name: Mapped[str]
    connection: Mapped[JsonObject] = mapped_column(JSONB)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


class MCPConnections(ABC):
    """One tier's connections, each under its name."""

    namespace: tuple[str, ...]
    scope: MCPScope

    @abstractmethod
    async def _value(self, name: str) -> JsonObject | None: ...

    @abstractmethod
    async def _values(self) -> Iterable[JsonObject]: ...

    @abstractmethod
    async def _put(self, name: str, value: JsonObject) -> None: ...

    @abstractmethod
    async def delete(self, name: str) -> None: ...

    async def put(self, record: MCPConnection) -> None:
        await self._put(record.name, record.model_dump(mode="json"))

    async def get(self, name: str) -> MCPConnection | None:
        value = await self._value(name)
        return None if value is None else MCPConnection.model_validate(value)

    async def records(self) -> list[MCPConnection]:
        """Every connection in name order, skipping unreadable ones so they cannot hide the rest."""
        records: list[MCPConnection] = []
        for value in await self._values():
            try:
                records.append(MCPConnection.model_validate(value))
            except ValidationError:
                logger.warning(
                    "Skipping unreadable MCP connection",
                    extra={"mcp_namespace": "/".join(self.namespace)},
                    exc_info=True,
                )
        return sorted(records, key=lambda record: record.name)

    def source(self, authorize: Callable[[], Awaitable[None]] | None = None) -> MCPSource:
        return MCPSource(
            scope=self.scope,
            namespace=self.namespace,
            list_connections=self.records,
            get_connection=self.get,
            authorize=authorize,
        )


class SharedMCPConnections(MCPConnections):
    """One workspace's connections."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace.strip().lower()
        self.scope = "workspace"
        self.namespace = (*WORKSPACE_MCPS_NAMESPACE, self.workspace)

    def _in_tier(self) -> ColumnElement[bool]:
        return MCPConnectionRow.workspace == self.workspace

    def _where(self, name: str | None = None) -> ColumnElement[bool]:
        return and_(self._in_tier(), *(() if name is None else (MCPConnectionRow.name == name,)))

    async def _value(self, name: str) -> JsonObject | None:
        async with postgres.session() as session:
            return await session.scalar(
                select(MCPConnectionRow.connection).where(self._where(name))
            )

    async def _values(self) -> Iterable[JsonObject]:
        async with postgres.session() as session:
            return list(
                await session.scalars(select(MCPConnectionRow.connection).where(self._where()))
            )

    async def _put(self, name: str, value: JsonObject) -> None:
        async with postgres.session() as session:
            upsert = insert(MCPConnectionRow).values(
                id=uuid7(), workspace=self.workspace, name=name, connection=value
            )
            # A workspace's names are unique through a partial index of their own.
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[MCPConnectionRow.workspace, MCPConnectionRow.name],
                    index_where=self._in_tier(),
                    set_={"connection": value, "updated_at": func.clock_timestamp()},
                )
            )

    async def delete(self, name: str) -> None:
        async with postgres.session() as session:
            await session.execute(delete(MCPConnectionRow).where(self._where(name)))


class UserMCPConnections(MCPConnections):
    """One person's own connections, used only in their runs."""

    _records = UserRecords(_PERSONAL_KIND)

    def __init__(self, login: str) -> None:
        self.login = login.strip().lower()
        self.scope = "user"
        self.namespace = (*USER_MCPS_NAMESPACE, self.login)

    async def _value(self, name: str) -> JsonObject | None:
        return await self._records.get(self.login, name)

    async def _values(self) -> Iterable[JsonObject]:
        return (await self._records.items(self.login)).values()

    async def _put(self, name: str, value: JsonObject) -> None:
        await self._records.put(self.login, value, name)

    async def delete(self, name: str) -> None:
        await self._records.delete(self.login, name)
