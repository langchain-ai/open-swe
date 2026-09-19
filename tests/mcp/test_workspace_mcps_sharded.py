from agent.mcp.models import MCPConnectionUpdate
from agent.mcp.workspace import (
    WORKSPACE_MCPS_NAMESPACE,
    list_workspace_mcps,
    save_workspace_mcp,
    workspace_mcp_source,
)
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
