"""Workspace policy adapter for the shared MCP runtime."""

from agent.dashboard.workspace_mcps import (
    get_workspace_mcp,
    prepare_workspace_mcp,
    workspace_mcp_source,
)
from agent.mcp import MCPConnectionUpdate, discover_tools


async def discover_workspace_mcp(
    name: str, update: MCPConnectionUpdate | None = None
) -> list[dict[str, str]]:
    """List tool descriptions for an admin to choose; never execute any tools."""
    record = (
        await prepare_workspace_mcp(name, update)
        if update is not None
        else await get_workspace_mcp(name)
    )
    if record is None:
        raise ValueError("Workspace MCP connection does not exist")
    definitions = await discover_tools(record, workspace_mcp_source.namespace)
    return [{"name": tool.name, "description": tool.description or ""} for tool in definitions]
