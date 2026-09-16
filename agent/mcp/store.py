"""MCP connections in PostgreSQL, one store per owner.

An owner is a GitHub login in the ``user`` scope or a workspace slug in the
``workspace`` scope, and either way it is resolved to the owning row's id on
every call — so a connection follows the person or the workspace rather than
the handle it was saved under.
"""

import logging
from collections.abc import Sequence
from typing import Literal
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import ColumnElement, delete, select

from agent.database import postgres
from agent.mcp.models import MCPConnection
from agent.mcp.rows import MCPConnectionColumns, UserMCPConnectionRow, WorkspaceMCPConnectionRow
from agent.store import StoreEntry, delete_value, now_iso, search_all_entries
from agent.users import User
from agent.workspaces.rows import WorkspaceRow
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG

logger = logging.getLogger(__name__)

type MCPScope = Literal["user", "workspace"]

USER_MCPS_NAMESPACE: list[str] = ["user_mcps"]
WORKSPACE_MCPS_NAMESPACE: list[str] = ["workspace_mcps"]

_SIGN_IN_AGAIN = "Sign in again before saving personal MCP connections"
_NO_WORKSPACE = "Workspace does not exist"


class MCPConnectionStore:
    """One owner's MCP connections in PostgreSQL."""

    def __init__(self, scope: MCPScope, owner: str) -> None:
        self.scope: MCPScope = scope
        self.owner = owner.strip().lower()

    async def get(self, name: str) -> MCPConnection | None:
        """The connection stored under ``name``, or ``None``.

        An unreadable row reads as a missing one, logged at error, the way
        :meth:`list_all` skips it: a record an older release wrote must not
        make every caller that resolves this connection fail.
        """
        rows = await self._rows(name)
        if not rows:
            return None
        try:
            return rows[0].to_connection()
        except ValidationError:
            logger.error(
                "Unreadable MCP connection record",
                extra={"mcp_scope": self.scope, "mcp_owner": self.owner, "mcp_name": name},
                exc_info=True,
            )
            return None

    async def exists(self, name: str) -> bool:
        """Whether a connection is stored under ``name``, readable or not."""
        return bool(await self._rows(name))

    async def list_all(self) -> list[MCPConnection]:
        """Every connection, by name; one unreadable row must not take the listing down."""
        records: list[MCPConnection] = []
        for row in await self._rows():
            try:
                records.append(row.to_connection())
            except ValidationError:
                logger.error(
                    "Skipping unreadable MCP connection record",
                    extra={"mcp_scope": self.scope, "mcp_owner": self.owner, "mcp_name": row.name},
                    exc_info=True,
                )
        return records

    async def put(self, name: str, record: MCPConnection) -> None:
        """Store ``record`` under ``name``, keeping the row's ``created_at``."""
        owner_id = await self._owner_id()
        if owner_id is None:
            raise ValueError(_SIGN_IN_AGAIN if self.scope == "user" else _NO_WORKSPACE)
        row_type = self._row_type()
        async with postgres.session() as session:
            row: MCPConnectionColumns | None = await session.scalar(
                select(row_type).where(self._owned_by(owner_id), row_type.name == name)
            )
            if row is None:
                row = self._new_row(owner_id, name)
                session.add(row)
            row.apply(record)

    async def delete(self, name: str) -> None:
        """Remove the connection stored under ``name``, if there is one."""
        owner_id = await self._owner_id()
        if owner_id is None:
            return
        row_type = self._row_type()
        async with postgres.session() as session:
            await session.execute(
                delete(row_type).where(self._owned_by(owner_id), row_type.name == name)
            )

    async def _owner_id(self) -> UUID | None:
        """The row this owner's connections hang off, or ``None`` when there is none."""
        if self.scope == "user":
            user = await User.for_login("github", self.owner)
            return None if user is None else user.id
        async with postgres.session() as session:
            return await session.scalar(
                select(WorkspaceRow.id).where(WorkspaceRow.slug == self.owner)
            )

    def _row_type(self) -> type[UserMCPConnectionRow] | type[WorkspaceMCPConnectionRow]:
        return UserMCPConnectionRow if self.scope == "user" else WorkspaceMCPConnectionRow

    def _owned_by(self, owner_id: UUID) -> ColumnElement[bool]:
        if self.scope == "user":
            return UserMCPConnectionRow.user_id == owner_id
        return WorkspaceMCPConnectionRow.workspace_id == owner_id

    def _new_row(self, owner_id: UUID, name: str) -> MCPConnectionColumns:
        if self.scope == "user":
            return UserMCPConnectionRow(user_id=owner_id, name=name)
        return WorkspaceMCPConnectionRow(workspace_id=owner_id, name=name)

    async def _rows(self, name: str | None = None) -> Sequence[MCPConnectionColumns]:
        """This owner's rows by name, or just the one named; empty for an unknown owner."""
        owner_id = await self._owner_id()
        if owner_id is None:
            return []
        row_type = self._row_type()
        query = select(row_type).where(self._owned_by(owner_id))
        if name is not None:
            query = query.where(row_type.name == name)
        async with postgres.session() as session:
            return list(await session.scalars(query.order_by(row_type.name)))


async def import_store_records() -> int:
    """Copy the MCP connections that still live in the LangGraph Store into PostgreSQL.

    Runs once per startup and returns how many rows it inserted. A name that
    already has a row keeps it, and every record that has been dealt with is
    deleted from the Store, so a second run has nothing left to do. A record
    that cannot be read, or whose owner has no row, stays where it is for the
    next startup rather than being dropped on the floor.
    """
    sources: tuple[tuple[MCPScope, list[str]], ...] = (
        ("user", USER_MCPS_NAMESPACE),
        ("workspace", WORKSPACE_MCPS_NAMESPACE),
    )
    imported = 0
    skipped = 0
    for scope, namespace in sources:
        for entry in await search_all_entries(namespace):
            owner = _owner_of(entry, namespace, scope)
            if owner is None:
                skipped += 1
                logger.error(
                    "Cannot tell which owner a stored MCP connection belongs to",
                    extra={"mcp_scope": scope, "store_namespace": namespace},
                )
                continue
            value = entry.value
            if _is_flat(entry, namespace):
                # Flat records predate the revision/updated_at bookkeeping; stamp
                # fresh values rather than reject a record that is otherwise sound.
                value = {"revision": uuid4().hex, "updated_at": now_iso(), **value}
            try:
                record = MCPConnection.model_validate(value)
            except ValidationError:
                skipped += 1
                logger.error(
                    "Skipping an unreadable stored MCP connection",
                    extra={"mcp_scope": scope, "mcp_owner": owner},
                    exc_info=True,
                )
                continue
            store = MCPConnectionStore(scope, owner)
            if not await store.exists(record.name):
                try:
                    await store.put(record.name, record)
                except ValueError:
                    skipped += 1
                    owner_field = "github_login" if scope == "user" else "workspace_slug"
                    logger.warning(
                        "Leaving an MCP connection whose owner has no row in the Store",
                        extra={owner_field: owner, "mcp_name": record.name},
                    )
                    continue
                imported += 1
            await delete_value(entry.namespace or namespace, record.name)
    logger.info(
        "Stored MCP connection records processed",
        extra={"imported_mcp_connections": imported, "skipped_mcp_connections": skipped},
    )
    return imported


def _is_flat(entry: StoreEntry, namespace: list[str]) -> bool:
    """Whether the entry lives at ``namespace`` itself rather than under an owner.

    A namespace the transport does not report counts as flat: the only search
    that reports none matches namespaces exactly.
    """
    return entry.namespace is None or len(entry.namespace) <= len(namespace)


def _owner_of(entry: StoreEntry, namespace: list[str], scope: MCPScope) -> str | None:
    """The owner an entry belongs to, or ``None`` when its namespace names none.

    A flat ``workspace_mcps`` record predates per-workspace namespaces and is
    the default workspace's; a flat ``user_mcps`` record names no login at all.
    """
    if _is_flat(entry, namespace):
        return DEFAULT_WORKSPACE_SLUG if scope == "workspace" else None
    return (entry.namespace or [])[len(namespace)]
