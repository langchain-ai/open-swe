import pytest

from agent.database import postgres
from agent.mcp.models import MCPConnectionUpdate
from agent.mcp.workspace import (
    WORKSPACE_MCPS_NAMESPACE,
    list_workspace_mcps,
    save_workspace_mcp,
    workspace_mcp_source,
)
from agent.workspaces.rows import WorkspaceRow

pytestmark = pytest.mark.usefixtures("registry_db")


def _update(name: str) -> MCPConnectionUpdate:
    return MCPConnectionUpdate(
        name=name, url="https://mcp.example.com/sse", transport="sse", enabled=True
    )


async def make_workspace(slug: str) -> None:
    async with postgres.session() as session:
        session.add(WorkspaceRow(slug=slug, name=slug))


async def test_connections_are_isolated_per_workspace() -> None:
    await make_workspace("oss")
    await save_workspace_mcp("default", "incident", _update("incident"))
    await save_workspace_mcp("oss", "docs", _update("docs"))
    assert [c["name"] for c in await list_workspace_mcps("default")] == ["incident"]
    assert [c["name"] for c in await list_workspace_mcps("oss")] == ["docs"]
    assert workspace_mcp_source("oss").namespace == (*WORKSPACE_MCPS_NAMESPACE, "oss")
