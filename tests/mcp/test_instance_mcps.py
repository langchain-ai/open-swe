import pytest

from agent.mcp import instance as instance_mcps
from agent.mcp.instance import (
    INSTANCE_MCPS_NAMESPACE,
    delete_instance_mcp,
    instance_mcp_source,
    list_instance_mcps,
    save_instance_mcp,
)
from agent.mcp.models import MCPConnectionUpdate
from agent.mcp.workspace import (
    WORKSPACE_MCPS_NAMESPACE,
    list_workspace_mcps,
    save_workspace_mcp,
)
from agent.store import StoreEntry
from tests.conftest import FakeStore


def _update(name: str) -> MCPConnectionUpdate:
    return MCPConnectionUpdate(
        name=name, url="https://mcp.example.com/sse", transport="sse", enabled=True
    )


async def test_instance_connections_are_their_own_tier(fake_store: FakeStore) -> None:
    await save_instance_mcp("shared", _update("shared"))
    await save_workspace_mcp("oss", "docs", _update("docs"))
    assert [c["name"] for c in await list_instance_mcps()] == ["shared"]
    assert [c["name"] for c in await list_workspace_mcps("oss")] == ["docs"]
    assert [c["name"] for c in await list_workspace_mcps("default")] == []
    assert instance_mcp_source().namespace == tuple(INSTANCE_MCPS_NAMESPACE)

    await delete_instance_mcp("shared")
    assert await list_instance_mcps() == []


def _flat_record(name: str) -> dict[str, object]:
    return {
        "name": name,
        "url": "https://mcp.example.com/sse",
        "transport": "sse",
        "enabled": True,
    }


async def test_pre_workspaces_records_are_the_instance_tier(fake_store: FakeStore) -> None:
    """The admin page's connections from before workspaces keep applying everywhere."""
    fake_store.seed(WORKSPACE_MCPS_NAMESPACE, "legacy", _flat_record("legacy"))
    assert [c["name"] for c in await list_instance_mcps()] == ["legacy"]
    assert await list_workspace_mcps("default") == []
    assert await list_workspace_mcps("oss") == []


async def test_adoption_deletes_the_flat_record_and_does_not_rerun(fake_store: FakeStore) -> None:
    fake_store.seed(WORKSPACE_MCPS_NAMESPACE, "legacy", _flat_record("legacy"))
    await list_instance_mcps()
    assert fake_store.values(WORKSPACE_MCPS_NAMESPACE) == {}
    assert [c["name"] for c in await list_instance_mcps()] == ["legacy"]


async def test_delete_adopts_a_flat_record_before_deleting(fake_store: FakeStore) -> None:
    """Deleting an unadopted record must not be a silent no-op that lets it reappear."""
    fake_store.seed(WORKSPACE_MCPS_NAMESPACE, "legacy", _flat_record("legacy"))
    await delete_instance_mcp("legacy")
    assert await list_instance_mcps() == []
    assert fake_store.values(WORKSPACE_MCPS_NAMESPACE) == {}


async def test_adoption_ignores_records_a_prefix_search_also_matches(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A namespace search can be prefix-based; a workspace's record must never be adopted.

    ``FakeStore`` matches namespaces exactly, so this drives the adoption through a
    stand-in for ``search_all_entries`` that reports a real backend's shape: a
    genuinely flat record alongside one that only surfaced because its namespace
    shares the flat namespace's prefix.
    """
    await save_workspace_mcp("oss", "docs", _update("docs"))

    async def fake_entries(namespace: list[str]) -> list[StoreEntry]:
        assert namespace == WORKSPACE_MCPS_NAMESPACE
        return [
            StoreEntry(WORKSPACE_MCPS_NAMESPACE, _flat_record("legacy")),
            StoreEntry([*WORKSPACE_MCPS_NAMESPACE, "oss"], _flat_record("docs")),
        ]

    monkeypatch.setattr(instance_mcps, "search_all_entries", fake_entries)
    assert [c["name"] for c in await list_instance_mcps()] == ["legacy"]
    assert [c["name"] for c in await list_workspace_mcps("oss")] == ["docs"]
