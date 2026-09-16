from agent.mcp.instance import (
    INSTANCE_MCPS_NAMESPACE,
    delete_instance_mcp,
    instance_mcp_source,
    list_instance_mcps,
    save_instance_mcp,
)
from agent.mcp.models import MCPConnectionUpdate
from agent.mcp.workspace import list_workspace_mcps, save_workspace_mcp
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
