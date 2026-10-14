"""Each tier's MCP connections in PostgreSQL, by name.

The instance's and each workspace's live in ``mcp_connection``, the instance's
without a workspace, and a person's in ``user_record``. A tier's ``namespace`` keys its discovery and OAuth token
caches, and names the LangGraph Store namespace its connections came from.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Collection, Iterable
from datetime import datetime
from uuid import UUID, uuid7

from pydantic import ValidationError
from sqlalchemy import ColumnElement, and_, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.mcp.models import MCPConnection
from openswe.mcp.runtime import MCPSource
from openswe.users.records import UserRecord, UserRecords, user_id_for_login
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

INSTANCE_MCPS_NAMESPACE = ("instance_mcps",)
WORKSPACE_MCPS_NAMESPACE = ("workspace_mcps",)
USER_MCPS_NAMESPACE = ("user_mcps",)
# Marks a value the LangGraph Store import wrote; any other write replaces the value without it.
STORE_COPY = "copied_from_store"
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

    @abstractmethod
    async def _value(self, name: str) -> JsonObject | None: ...

    @abstractmethod
    async def _values(self) -> Iterable[JsonObject]: ...

    @abstractmethod
    async def _put(self, name: str, value: JsonObject) -> None: ...

    @abstractmethod
    async def delete(self, name: str) -> None: ...

    @abstractmethod
    async def written_at(self, name: str) -> datetime | None:
        """When PostgreSQL last wrote the connection, which the Store import compares against."""

    async def put(self, record: MCPConnection, *, store_copy: bool = False) -> None:
        value: JsonObject = record.model_dump(mode="json")
        if store_copy:
            value[STORE_COPY] = True
        await self._put(record.name, value)

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
            namespace=self.namespace,
            list_connections=self.records,
            get_connection=self.get,
            authorize=authorize,
        )


class SharedMCPConnections(MCPConnections):
    """The instance tier's connections, or one workspace's."""

    def __init__(self, workspace: str | None = None) -> None:
        """``workspace`` is a workspace's slug, or ``None`` for the instance tier."""
        self.workspace = None if workspace is None else workspace.strip().lower()
        self.namespace = (
            INSTANCE_MCPS_NAMESPACE
            if self.workspace is None
            else (*WORKSPACE_MCPS_NAMESPACE, self.workspace)
        )

    def _in_tier(self) -> ColumnElement[bool]:
        if self.workspace is None:
            return MCPConnectionRow.workspace.is_(None)
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
            # Each tier's names are unique through a partial index of its own.
            index_elements = (
                [MCPConnectionRow.name]
                if self.workspace is None
                else [MCPConnectionRow.workspace, MCPConnectionRow.name]
            )
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=index_elements,
                    index_where=self._in_tier(),
                    set_={"connection": value, "updated_at": func.clock_timestamp()},
                )
            )

    async def delete(self, name: str) -> None:
        async with postgres.session() as session:
            await session.execute(delete(MCPConnectionRow).where(self._where(name)))

    async def written_at(self, name: str) -> datetime | None:
        async with postgres.session() as session:
            return await session.scalar(
                select(MCPConnectionRow.updated_at).where(self._where(name))
            )


INSTANCE_CONNECTIONS = SharedMCPConnections()


class UserMCPConnections(MCPConnections):
    """One person's own connections, used only in their runs."""

    _records = UserRecords(_PERSONAL_KIND)

    def __init__(self, login: str) -> None:
        self.login = login.strip().lower()
        self.namespace = (*USER_MCPS_NAMESPACE, self.login)

    async def _value(self, name: str) -> JsonObject | None:
        return await self._records.get(self.login, name)

    async def _values(self) -> Iterable[JsonObject]:
        return (await self._records.items(self.login)).values()

    async def _put(self, name: str, value: JsonObject) -> None:
        await self._records.put(self.login, value, name)

    async def delete(self, name: str) -> None:
        await self._records.delete(self.login, name)

    async def written_at(self, name: str) -> datetime | None:
        return await self._records.written_at(self.login, name)


async def delete_store_copies(
    present: Collection[tuple[tuple[str, ...], str]], copied_after: datetime
) -> int:
    """Delete connections still as the Store import copied them after ``copied_after``
    whose Store record, keyed by ``(namespace, name)`` in ``present``, is gone.

    A replica of the previous release deletes a connection only from the Store.
    """
    deleted = 0
    async with postgres.session() as session:
        shared = await session.execute(
            select(MCPConnectionRow.id, MCPConnectionRow.workspace, MCPConnectionRow.name).where(
                MCPConnectionRow.connection.has_key(STORE_COPY),
                MCPConnectionRow.updated_at > copied_after,
            )
        )
        for row_id, workspace, name in shared.tuples().all():
            if (SharedMCPConnections(workspace).namespace, name) in present:
                continue
            gone = await session.scalar(
                delete(MCPConnectionRow)
                .where(
                    MCPConnectionRow.id == row_id,
                    MCPConnectionRow.connection.has_key(STORE_COPY),
                )
                .returning(MCPConnectionRow.id)
            )
            deleted += gone is not None
        logins = {namespace[1] for namespace, _ in present if namespace[:1] == USER_MCPS_NAMESPACE}
        kept = {
            (user_id, name)
            for login in logins
            if (user_id := await session.scalar(select(user_id_for_login(login)))) is not None
            for namespace, name in present
            if namespace == (*USER_MCPS_NAMESPACE, login)
        }
        personal = await session.execute(
            select(UserRecord.user_id, UserRecord.key).where(
                UserRecord.kind == _PERSONAL_KIND,
                UserRecord.value.has_key(STORE_COPY),
                UserRecord.updated_at > copied_after,
            )
        )
        for user_id, name in personal.tuples().all():
            if (user_id, name) in kept:
                continue
            gone = await session.scalar(
                delete(UserRecord)
                .where(
                    UserRecord.user_id == user_id,
                    UserRecord.kind == _PERSONAL_KIND,
                    UserRecord.key == name,
                    UserRecord.value.has_key(STORE_COPY),
                )
                .returning(UserRecord.key)
            )
            deleted += gone is not None
    return deleted
