"""Where the MCP server's OAuth proxy keeps client registrations and tokens.

Every replica answers any OAuth step, so the state lives in PostgreSQL, one
table per collection the proxy uses. Each row's ``entry`` is the whole
key-value entry as a readable document; every field of the stored model that
its table does not list as plaintext is sealed with ``TOKEN_ENCRYPTION_KEY``,
so a field a FastMCP upgrade adds is sealed until someone reviews it. An entry
reads as absent once it expires, and each write sweeps a few expired rows from
its table.
"""

import hashlib
import json
import logging
from collections.abc import Collection, Mapping
from datetime import datetime
from typing import ClassVar, Final, override
from uuid import UUID, uuid7

from key_value.aio.errors import DeserializationError
from key_value.aio.stores.base import BaseStore, ManagedEntry
from sqlalchemy import ColumnElement, Computed, ForeignKey, delete, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.encryption import decrypt_token, encrypt_token
from openswe.store import delete_value
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

# Where the previous release kept these entries, in the LangGraph Store.
STORE_NAMESPACE: Final = ("mcp_server_oauth",)
_SWEEP_LIMIT: Final = 100


class MCPOAuthRow(Base):
    """One entry of a collection; each collection has a table of its own."""

    __abstract__ = True

    collection: ClassVar[str]
    # The stored model's fields kept readable; every other one is sealed.
    plaintext: ClassVar[frozenset[str]]
    # Whether the entry's key is itself a credential, kept only as its SHA-256.
    hashes_key: ClassVar[bool] = False

    entry: Mapped[JsonObject] = mapped_column(JSONB)
    expires_at: Mapped[datetime | None]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    key: Mapped[str] = mapped_column(Computed("entry->>'key'", persisted=True), init=False)
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

    @classmethod
    def stored_key(cls, key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest() if cls.hashes_key else key

    @classmethod
    def seal(cls, value: JsonObject) -> JsonObject:
        return {
            name: field
            if name in cls.plaintext or field is None
            else {"sealed": encrypt_token(json.dumps(field))}
            for name, field in value.items()
        }

    @classmethod
    def open(cls, value: JsonObject) -> JsonObject | None:
        """``value`` with its sealed fields opened, or ``None`` if one does not open."""
        opened: JsonObject = {}
        for name, field in value.items():
            match field:
                case {"sealed": str(sealed)} if len(field) == 1:
                    text = decrypt_token(sealed)
                    if not text:
                        return None
                    opened[name] = json.loads(text)
                case _ if name in cls.plaintext or field is None:
                    opened[name] = field
                case _:
                    logger.warning(
                        "MCP OAuth entry has an unsealed secret field",
                        extra={"oauth_collection": cls.collection, "oauth_field": name},
                    )
                    return None
        return opened


class MCPOAuthClientRow(MCPOAuthRow):
    __tablename__ = "mcp_oauth_client"
    collection = "mcp-oauth-proxy-clients"
    plaintext = frozenset(
        {
            "client_id",
            "client_name",
            "client_uri",
            "logo_uri",
            "tos_uri",
            "policy_uri",
            "jwks_uri",
            "redirect_uris",
            "allowed_redirect_uri_patterns",
            "grant_types",
            "response_types",
            "scope",
            "token_endpoint_auth_method",
            "application_type",
            "client_id_issued_at",
            "client_secret_expires_at",
            "issuer",
            "software_id",
            "software_version",
            "cimd_fetched_at",
        }
    )


class MCPOAuthTransactionRow(MCPOAuthRow):
    __tablename__ = "mcp_oauth_transaction"
    collection = "mcp-oauth-transactions"
    plaintext = frozenset(
        {
            "txn_id",
            "client_id",
            "client_redirect_uri",
            "code_challenge",
            "code_challenge_method",
            "scopes",
            "created_at",
            "resource",
            "csrf_expires_at",
        }
    )


class MCPOAuthAuthorizationCodeRow(MCPOAuthRow):
    __tablename__ = "mcp_oauth_authorization_code"
    collection = "mcp-authorization-codes"
    plaintext = frozenset(
        {
            "client_id",
            "redirect_uri",
            "code_challenge",
            "code_challenge_method",
            "scopes",
            "expires_at",
            "created_at",
        }
    )
    # The proxy keys a code by the code itself.
    hashes_key = True


class MCPOAuthUpstreamTokenRow(MCPOAuthRow):
    __tablename__ = "mcp_oauth_upstream_token"
    collection = "mcp-upstream-tokens"
    plaintext = frozenset(
        {
            "upstream_token_id",
            "refresh_token_expires_at",
            "expires_at",
            "token_type",
            "scope",
            "client_id",
            "created_at",
        }
    )


class MCPOAuthRefreshTokenRow(MCPOAuthRow):
    __tablename__ = "mcp_oauth_refresh_token"
    collection = "mcp-refresh-tokens"
    plaintext = frozenset({"client_id", "scopes", "expires_at", "created_at"})


class MCPOAuthJTIMappingRow(MCPOAuthRow):
    __tablename__ = "mcp_oauth_jti_mapping"
    collection = "mcp-jti-mappings"
    plaintext = frozenset({"jti", "upstream_token_id", "created_at"})


class MCPOAuthConsentCSRFTokenRow(MCPOAuthRow):
    __tablename__ = "mcp_oauth_consent_csrf_token"
    collection = "mcp-consent-csrf-tokens"
    plaintext = frozenset({"txn_id", "expires_at"})


# The collections FastMCP's OAuth proxy keeps its state in.
TABLES: Final[Mapping[str, type[MCPOAuthRow]]] = {
    table.collection: table
    for table in (
        MCPOAuthClientRow,
        MCPOAuthTransactionRow,
        MCPOAuthAuthorizationCodeRow,
        MCPOAuthUpstreamTokenRow,
        MCPOAuthRefreshTokenRow,
        MCPOAuthJTIMappingRow,
        MCPOAuthConsentCSRFTokenRow,
    )
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

    def open_store_record(self, sealed: str) -> ManagedEntry | None:
        """The entry a LangGraph Store record sealed whole; one that does not open reads as absent."""
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
            document = await session.scalar(
                select(table.entry).where(table.key == table.stored_key(key), _live(table))
            )
        if document is None:
            return None
        value = document.get("value")
        opened = table.open(value) if isinstance(value, dict) else None
        # An entry that no longer opens reads as absent, so the client signs in again.
        if opened is None:
            return None
        return self._serialization_adapter.load_dict({**document, "value": opened})

    @override
    async def _put_managed_entry(
        self, *, collection: str, key: str, managed_entry: ManagedEntry
    ) -> None:
        await _delete_store_copy(collection, key)
        await self.save(collection, key, managed_entry)

    async def save(
        self, collection: str, key: str, managed_entry: ManagedEntry, *, store_copy: bool = False
    ) -> None:
        table = table_for(collection)
        document = self._serialization_adapter.dump_dict(
            managed_entry, key=table.stored_key(key), collection=collection
        )
        document["value"] = table.seal(managed_entry.value_as_dict)
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
                entry=document,
                expires_at=managed_entry.expires_at,
                copied_from_store=store_copy,
            )
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[table.key],
                    set_={
                        "entry": document,
                        "expires_at": managed_entry.expires_at,
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
                delete(table).where(table.key == table.stored_key(key)).returning(_live(table))
            )
        return bool(existed)

    async def written_at(self, collection: str, key: str) -> datetime | None:
        """When PostgreSQL last wrote the entry, which the Store import compares against."""
        table = table_for(collection)
        async with postgres.session() as session:
            return await session.scalar(
                select(table.updated_at).where(table.key == table.stored_key(key))
            )


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
            kept = {table.stored_key(key) for name, key in present if name == collection}
            copies = await session.execute(
                select(table.id, table.key).where(
                    table.copied_from_store, table.updated_at > copied_after
                )
            )
            gone = [row_id for row_id, key in copies if key not in kept]
            if gone:
                removed = await session.scalars(
                    delete(table)
                    .where(table.id.in_(gone), table.copied_from_store)
                    .returning(table.id)
                )
                deleted += len(removed.all())
    return deleted
