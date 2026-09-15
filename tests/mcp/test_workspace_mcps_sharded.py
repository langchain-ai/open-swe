import pytest

from agent.mcp import workspace as workspace_mcps
from agent.mcp.models import MCPConnectionUpdate
from agent.mcp.workspace import (
    WORKSPACE_MCPS_NAMESPACE,
    delete_workspace_mcp,
    list_workspace_mcps,
    save_workspace_mcp,
    workspace_mcp_source,
)
from agent.store import StoreEntry
from tests.conftest import FakeStore


def _update(name: str) -> MCPConnectionUpdate:
    return MCPConnectionUpdate(
        name=name, url="https://mcp.example.com/sse", transport="sse", enabled=True
    )


async def test_connections_are_isolated_per_workspace(fake_store: FakeStore) -> None:
    await save_workspace_mcp("default", "incident", _update("incident"))
    await save_workspace_mcp("oss", "docs", _update("docs"))
    assert [c["name"] for c in await list_workspace_mcps("default")] == ["incident"]
    assert [c["name"] for c in await list_workspace_mcps("oss")] == ["docs"]
    assert workspace_mcp_source("oss").namespace == (*WORKSPACE_MCPS_NAMESPACE, "oss")


async def test_legacy_flat_records_belong_to_default(fake_store: FakeStore) -> None:
    fake_store.seed(
        WORKSPACE_MCPS_NAMESPACE,
        "legacy",
        {
            "name": "legacy",
            "url": "https://mcp.example.com/sse",
            "transport": "sse",
            "enabled": True,
        },
    )
    assert [c["name"] for c in await list_workspace_mcps("default")] == ["legacy"]
    assert await list_workspace_mcps("oss") == []


async def test_migration_deletes_the_flat_record_and_does_not_rerun(fake_store: FakeStore) -> None:
    fake_store.seed(
        WORKSPACE_MCPS_NAMESPACE,
        "legacy",
        {
            "name": "legacy",
            "url": "https://mcp.example.com/sse",
            "transport": "sse",
            "enabled": True,
        },
    )
    await list_workspace_mcps("default")
    assert fake_store.values(WORKSPACE_MCPS_NAMESPACE) == {}
    assert [c["name"] for c in await list_workspace_mcps("default")] == ["legacy"]


async def test_delete_migrates_legacy_default_before_deleting(fake_store: FakeStore) -> None:
    """Deleting an unmigrated legacy record must not be a silent no-op.

    Without migrating first, the delete targets the nested default-workspace
    namespace while the record still lives in the flat legacy namespace, so it
    reappears the next time the default workspace is read.
    """
    fake_store.seed(
        WORKSPACE_MCPS_NAMESPACE,
        "legacy",
        {
            "name": "legacy",
            "url": "https://mcp.example.com/sse",
            "transport": "sse",
            "enabled": True,
        },
    )
    await delete_workspace_mcp("default", "legacy")
    assert await list_workspace_mcps("default") == []
    assert fake_store.values(WORKSPACE_MCPS_NAMESPACE) == {}


async def test_migration_ignores_records_a_prefix_search_also_matches(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A namespace search can be prefix-based; a nested item must never migrate as legacy.

    ``FakeStore`` matches namespaces exactly, so this drives ``_migrate_legacy_default``
    through a stand-in for ``search_all_entries`` that reports a real backend's shape:
    a genuinely flat record alongside one that only surfaced because its namespace
    shares the flat namespace's prefix.
    """
    await save_workspace_mcp("oss", "docs", _update("docs"))

    def value(name: str) -> dict[str, object]:
        return {
            "name": name,
            "url": "https://mcp.example.com/sse",
            "transport": "sse",
            "enabled": True,
        }

    async def fake_entries(namespace: list[str]) -> list[StoreEntry]:
        assert namespace == WORKSPACE_MCPS_NAMESPACE
        return [
            StoreEntry(WORKSPACE_MCPS_NAMESPACE, value("legacy")),
            StoreEntry([*WORKSPACE_MCPS_NAMESPACE, "oss"], value("docs")),
        ]

    monkeypatch.setattr(workspace_mcps, "search_all_entries", fake_entries)
    assert [c["name"] for c in await list_workspace_mcps("default")] == ["legacy"]
    assert [c["name"] for c in await list_workspace_mcps("oss")] == ["docs"]
