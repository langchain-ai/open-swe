from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from fastmcp.server.auth.providers.github import GitHubProvider
from key_value.aio.adapters.pydantic.base import BasePydanticAdapter
from key_value.aio.stores.base import ManagedEntry
from sqlalchemy import select, update

from openswe.database import postgres, store_imports
from openswe.database.store_imports import StoreImport
from openswe.encryption import encrypt_token
from openswe.mcp import import_oauth_entries
from openswe.mcp.import_oauth_entries import import_store_mcp_oauth_entries
from openswe.mcp.token_store import (
    STORE_NAMESPACE,
    TABLES,
    MCPOAuthUpstreamTokenRow,
    SealedStore,
    UnknownOAuthCollection,
)
from openswe.store import StoreEntry
from tests.conftest import FakeStore

_COLLECTION = "mcp-upstream-tokens"


async def test_every_collection_the_oauth_proxy_uses_has_a_table() -> None:
    proxy = GitHubProvider(
        client_id="client",
        client_secret="secret",
        base_url="https://openswe.example",
        client_storage=SealedStore(),
    )
    used = {
        adapter._default_collection
        for adapter in vars(proxy).values()
        if isinstance(adapter, BasePydanticAdapter)
    }
    assert used == set(TABLES)
    with pytest.raises(UnknownOAuthCollection):
        await SealedStore().get("key", collection="mcp-added-by-an-upgrade")


@pytest.fixture
def store(registry_db: None, fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    """``fake_store`` searched by namespace prefix, reporting each entry's own namespace, as the real Store does."""
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())

    async def search(namespace: Sequence[str]) -> list[StoreEntry]:
        return [
            StoreEntry(list(item_namespace), dict(value), key)
            for item_namespace, items in fake_store.items.items()
            if item_namespace[: len(namespace)] == tuple(namespace)
            for key, value in items.items()
        ]

    monkeypatch.setattr(import_oauth_entries, "search_all_entries", search)
    return fake_store


def _seed(
    store: FakeStore,
    key: str,
    token: str,
    *,
    saved: timedelta = timedelta(),
    expires: timedelta | None = None,
) -> None:
    """Seed an entry as the previous release saved it, ``saved`` ago, expiring ``expires`` from now."""
    now = datetime.now(UTC)
    entry = ManagedEntry(
        value={"token": token},
        created_at=now - saved,
        expires_at=None if expires is None else now + expires,
    )
    sealed = encrypt_token(SealedStore()._serialization_adapter.dump_json(entry))
    store.seed([*STORE_NAMESPACE, _COLLECTION], key, {"sealed": sealed})


async def _token(key: str) -> object:
    value = await SealedStore().get(key, collection=_COLLECTION)
    return None if value is None else value["token"]


async def test_entries_read_as_absent_once_expired_and_later_writes_sweep_them(
    store: FakeStore,
) -> None:
    sealed_store = SealedStore()
    await sealed_store.put("lapsing", {"token": "secret"}, collection=_COLLECTION, ttl=60)
    assert await _token("lapsing") == "secret"
    async with postgres.session() as session:
        sealed = (await session.execute(select(MCPOAuthUpstreamTokenRow.sealed_entry))).scalar_one()
        assert "secret" not in sealed
        await session.execute(
            update(MCPOAuthUpstreamTokenRow).values(
                expires_at=datetime.now(UTC) - timedelta(seconds=1)
            )
        )

    assert await _token("lapsing") is None
    await sealed_store.put("other", {"token": "other"}, collection=_COLLECTION)
    async with postgres.session() as session:
        assert list(await session.scalars(select(MCPOAuthUpstreamTokenRow.key))) == ["other"]


async def test_store_import_copies_live_entries_then_retires_the_store_records(
    store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(store, "client", "registered", saved=timedelta(hours=1))
    _seed(store, "session", "live", saved=timedelta(hours=1), expires=timedelta(hours=1))
    _seed(store, "lapsed", "expired", saved=timedelta(hours=2), expires=-timedelta(minutes=1))

    assert await import_store_mcp_oauth_entries() == StoreImport(moved=2)
    assert await _token("client") == "registered"
    assert await _token("session") == "live"
    assert await _token("lapsed") is None
    assert await import_store_mcp_oauth_entries() == StoreImport(waiting=2)

    _seed(store, "client", "re-registered")
    assert await import_store_mcp_oauth_entries() == StoreImport(moved=1, waiting=1)
    assert await _token("client") == "re-registered"

    await SealedStore().delete("session", collection=_COLLECTION)
    monkeypatch.setattr(store_imports, "RETIRE_STORE_COPY_AFTER", timedelta())
    assert await import_store_mcp_oauth_entries() == StoreImport(moved=1)
    assert await _token("session") is None
    assert await _token("client") == "re-registered"
    assert set(store.values([*STORE_NAMESPACE, _COLLECTION])) == {"lapsed"}


async def test_store_import_drops_copies_an_old_replica_deleted_from_the_store(
    store: FakeStore,
) -> None:
    _seed(store, "revoked", "old")
    _seed(store, "refreshed", "old")
    assert await import_store_mcp_oauth_entries() == StoreImport(moved=2)
    await SealedStore().put("refreshed", {"token": "new"}, collection=_COLLECTION)

    store.items[(*STORE_NAMESPACE, _COLLECTION)].pop("revoked")
    assert await import_store_mcp_oauth_entries() == StoreImport(moved=1)
    assert await _token("revoked") is None
    assert await _token("refreshed") == "new"
