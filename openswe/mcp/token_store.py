"""Where the MCP server's OAuth proxy keeps client registrations and tokens.

Every replica answers any OAuth step, so the state lives in PostgreSQL, one
table per collection the proxy uses, sealed with ``TOKEN_ENCRYPTION_KEY``
because it holds upstream GitHub tokens. An entry reads as absent once it
expires, and each write sweeps a few expired rows from its table.
"""

import logging
from collections.abc import Collection, Mapping
from datetime import datetime
from typing import Final, override
from uuid import UUID, uuid7

from key_value.aio.errors import DeserializationError
from key_value.aio.stores.base import BaseStore, ManagedEntry
from sqlalchemy import ColumnElement, ForeignKey, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.encryption import decrypt_token, encrypt_token
from openswe.store import delete_value

logger = logging.getLogger(__name__)

# Where the previous release kept these entries, in the LangGraph Store.
STORE_NAMESPACE: Final = ("mcp_server_oauth",)
_SWEEP_LIMIT: Final = 100


class MCPOAuthRow(Base):
    """One sealed entry of a collection; each collection has a table of its own."""

    __abstract__ = True

    key: Mapped[str]
    sealed_entry: Mapped[str]
    expires_at: Mapped[datetime | None]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    copied_from_store: Mapped[bool] = mapped_column(server_default="false", default=False)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    # No person is behind the proxy's writes, so these stay NULL.
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )


class MCPOAuthClientRow(MCPOAuthRow):
    __tablename__ = "mcp_oauth_client"


class MCPOAuthTransactionRow(MCPOAuthRow):
    __tablename__ = "mcp_oauth_transaction"


class MCPOAuthAuthorizationCodeRow(MCPOAuthRow):
    __tablename__ = "mcp_oauth_authorization_code"


class MCPOAuthUpstreamTokenRow(MCPOAuthRow):
    __tablename__ = "mcp_oauth_upstream_token"


class MCPOAuthRefreshTokenRow(MCPOAuthRow):
    __tablename__ = "mcp_oauth_refresh_token"


class MCPOAuthJTIMappingRow(MCPOAuthRow):
    __tablename__ = "mcp_oauth_jti_mapping"


class MCPOAuthConsentCSRFTokenRow(MCPOAuthRow):
    __tablename__ = "mcp_oauth_consent_csrf_token"


# The collections FastMCP's OAuth proxy keeps its state in.
TABLES: Final[Mapping[str, type[MCPOAuthRow]]] = {
    "mcp-oauth-proxy-clients": MCPOAuthClientRow,
    "mcp-oauth-transactions": MCPOAuthTransactionRow,
    "mcp-authorization-codes": MCPOAuthAuthorizationCodeRow,
    "mcp-upstream-tokens": MCPOAuthUpstreamTokenRow,
    "mcp-refresh-tokens": MCPOAuthRefreshTokenRow,
    "mcp-jti-mappings": MCPOAuthJTIMappingRow,
    "mcp-consent-csrf-tokens": MCPOAuthConsentCSRFTokenRow,
}


class UnknownOAuthCollection(LookupError):
    """A collection with no table, such as one a FastMCP upgrade added."""


def table_for(collection: str) -> type[MCPOAuthRow]:
    try:
        return TABLES[collection]
    except KeyError:
        raise UnknownOAuthCollection(collection) from None


def _live(table: type[MCPOAuthRow]) -> ColumnElement[bool]:
    return or_(table.expires_at.is_(None), table.expires_at > func.clock_timestamp())


class SealedStore(BaseStore):
    def __init__(self) -> None:
        super().__init__(stable_api=True)

    def open(self, sealed: str) -> ManagedEntry | None:
        """The entry ``sealed`` holds; one sealed under a retired key or unreadable reads as absent."""
        opened = decrypt_token(sealed)
        if not opened:
            return None
        try:
            return self._serialization_adapter.load_json(opened)
        except DeserializationError:
            logger.warning("Failed to read a sealed MCP OAuth entry", exc_info=True)
            return None

    @override
    async def _get_managed_entry(self, *, collection: str, key: str) -> ManagedEntry | None:
        table = table_for(collection)
        async with postgres.session() as session:
            sealed = await session.scalar(
                select(table.sealed_entry).where(table.key == key, _live(table))
            )
        # An entry that no longer opens reads as absent, so the client signs in again.
        return None if sealed is None else self.open(sealed)

    @override
    async def _put_managed_entry(
        self, *, collection: str, key: str, managed_entry: ManagedEntry
    ) -> None:
        await _delete_store_copy(collection, key)
        sealed = encrypt_token(self._serialization_adapter.dump_json(managed_entry))
        await self.put_sealed(collection, key, sealed, managed_entry.expires_at)

    async def put_sealed(
        self,
        collection: str,
        key: str,
        sealed: str,
        expires_at: datetime | None,
        *,
        store_copy: bool = False,
    ) -> None:
        table = table_for(collection)
        async with postgres.session() as session:
            expired = (
                select(table.id)
                .where(table.expires_at <= func.clock_timestamp())
                .limit(_SWEEP_LIMIT)
                .with_for_update(skip_locked=True)
            )
            await session.execute(delete(table).where(table.id.in_(expired)))
            upsert = insert(table).values(
                id=uuid7(),
                key=key,
                sealed_entry=sealed,
                expires_at=expires_at,
                copied_from_store=store_copy,
            )
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[table.key],
                    set_={
                        "sealed_entry": sealed,
                        "expires_at": expires_at,
                        "copied_from_store": store_copy,
                        "updated_at": func.clock_timestamp(),
                        "updated_by": None,
                    },
                )
            )

    @override
    async def _delete_managed_entry(self, *, key: str, collection: str) -> bool:
        table = table_for(collection)
        await _delete_store_copy(collection, key)
        async with postgres.session() as session:
            existed = await session.scalar(
                delete(table).where(table.key == key).returning(_live(table))
            )
        return bool(existed)

    async def written_at(self, collection: str, key: str) -> datetime | None:
        """When PostgreSQL last wrote the entry, which the Store import compares against."""
        table = table_for(collection)
        async with postgres.session() as session:
            return await session.scalar(select(table.updated_at).where(table.key == key))


async def _delete_store_copy(collection: str, key: str) -> None:
    """Delete the entry's Store record first, so the Store import cannot bring back what this replaces."""
    await delete_value((*STORE_NAMESPACE, collection), key)


async def delete_store_copies(present: Collection[tuple[str, str]], copied_after: datetime) -> int:
    """Delete entries still as the Store import copied them after ``copied_after``
    whose Store record, keyed by ``(collection, key)`` in ``present``, is gone.

    A replica of the previous release deletes an entry only from the Store.
    """
    deleted = 0
    async with postgres.session() as session:
        for collection, table in TABLES.items():
            copies = await session.execute(
                select(table.id, table.key).where(
                    table.copied_from_store, table.updated_at > copied_after
                )
            )
            gone = [row_id for row_id, key in copies if (collection, key) not in present]
            if gone:
                removed = await session.scalars(
                    delete(table)
                    .where(table.id.in_(gone), table.copied_from_store)
                    .returning(table.id)
                )
                deleted += len(removed.all())
    return deleted
