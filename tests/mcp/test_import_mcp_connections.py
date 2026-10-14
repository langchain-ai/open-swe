from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from openswe.database import store_imports
from openswe.database.store_imports import StoreImport
from openswe.mcp import import_records
from openswe.mcp.import_records import import_store_mcp_connections
from openswe.mcp.instance import delete_instance_mcp, list_instance_mcps
from openswe.mcp.models import MCPConnectionUpdate
from openswe.mcp.user import list_user_mcps
from openswe.mcp.workspace import list_workspace_mcps, save_workspace_mcp
from openswe.store import StoreEntry
from openswe.users.models import User
from tests.conftest import FakeStore


@pytest.fixture
def store(registry_db: None, fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    """``fake_store`` searched by namespace prefix, reporting each entry's own namespace, as the real Store does."""

    async def search(namespace: Sequence[str]) -> list[StoreEntry]:
        return [
            StoreEntry(list(item_namespace), dict(value), key)
            for item_namespace, items in fake_store.items.items()
            if item_namespace[: len(namespace)] == tuple(namespace)
            for key, value in items.items()
        ]

    monkeypatch.setattr(import_records, "search_all_entries", search)
    return fake_store


def _record(name: str, url: str = "https://mcp.example.com/mcp", **fields: str) -> dict[str, str]:
    return {"name": name, "url": url, **fields}


def _urls(connections: list[dict[str, object]]) -> dict[object, object]:
    return {connection["name"]: connection["url"] for connection in connections}


async def test_store_import_copies_every_tier_then_retires_the_store_records(
    store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice")
    await User.sign_in("github", "1", login="alice")
    await save_workspace_mcp(
        "core", "docs", MCPConnectionUpdate(name="docs", url="https://postgres.example/mcp")
    )
    saved = "2026-01-01T00:00:00+00:00"
    store.seed(["instance_mcps"], "shared", _record("shared", revision="r", updated_at=saved))
    store.seed(["workspace_mcps"], "legacy", _record("legacy"))
    store.seed(["workspace_mcps"], "shared", _record("shared", "https://shadowed.example/mcp"))
    store.seed(["workspace_mcps", "oss"], "docs", _record("docs", updated_at=saved))
    store.seed(["workspace_mcps", "core"], "docs", _record("docs", "https://older.example/mcp"))
    store.seed(["user_mcps", "alice"], "linear", _record("linear", updated_at=saved))
    store.seed(["user_mcps", "nobody"], "linear", _record("linear"))

    assert await import_store_mcp_connections() == StoreImport(moved=4, waiting=3)
    assert _urls(await list_instance_mcps()) == {
        "legacy": "https://mcp.example.com/mcp",
        "shared": "https://mcp.example.com/mcp",
    }
    assert _urls(await list_workspace_mcps("oss")) == {"docs": "https://mcp.example.com/mcp"}
    assert _urls(await list_workspace_mcps("core")) == {"docs": "https://postgres.example/mcp"}
    assert [connection["name"] for connection in await list_user_mcps("alice")] == ["linear"]
    assert await import_store_mcp_connections() == StoreImport(waiting=7)

    store.seed(
        ["instance_mcps"],
        "shared",
        _record(
            "shared", "https://old-replica.example/mcp", updated_at=datetime.now(UTC).isoformat()
        ),
    )
    assert await import_store_mcp_connections() == StoreImport(moved=1, waiting=6)
    assert _urls(await list_instance_mcps())["shared"] == "https://old-replica.example/mcp"

    await delete_instance_mcp("legacy")
    monkeypatch.setattr(store_imports, "RETIRE_STORE_COPY_AFTER", timedelta())
    assert await import_store_mcp_connections() == StoreImport(moved=5, waiting=1)
    assert [connection["name"] for connection in await list_instance_mcps()] == ["shared"]
    assert {namespace for namespace, items in store.items.items() if items} == {
        ("user_mcps", "nobody")
    }


async def test_store_import_drops_copies_an_old_replica_deleted_from_the_store(
    store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice")
    await User.sign_in("github", "1", login="alice")
    store.seed(["instance_mcps"], "revoked", _record("revoked"))
    store.seed(["workspace_mcps", "oss"], "revoked", _record("revoked"))
    store.seed(["workspace_mcps", "oss"], "edited", _record("edited"))
    store.seed(["user_mcps", "alice"], "revoked", _record("revoked"))
    assert await import_store_mcp_connections() == StoreImport(moved=4)
    await save_workspace_mcp(
        "oss", "edited", MCPConnectionUpdate(name="edited", url="https://edited.example/mcp")
    )
    await save_workspace_mcp(
        "core", "fresh", MCPConnectionUpdate(name="fresh", url="https://fresh.example/mcp")
    )

    for namespace in [("instance_mcps",), ("workspace_mcps", "oss"), ("user_mcps", "alice")]:
        store.items[namespace].pop("revoked")
    store.items[("workspace_mcps", "oss")].pop("edited")

    assert await import_store_mcp_connections() == StoreImport(moved=3)
    assert await list_instance_mcps() == []
    assert _urls(await list_workspace_mcps("oss")) == {"edited": "https://edited.example/mcp"}
    assert _urls(await list_workspace_mcps("core")) == {"fresh": "https://fresh.example/mcp"}
    assert await list_user_mcps("alice") == []
