"""Workspace policy adapter for the shared MCP runtime."""

from langchain_core.tools import BaseTool

from agent.dashboard.workspace_mcps import (
    get_workspace_mcp,
    prepare_workspace_mcp,
    workspace_mcp_source,
)
from agent.mcp import MCPConnectionUpdate, discover_tools, load_mcp_tools


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


async def load_workspace_mcp_tools(*, connection_name: str | None = None) -> list[BaseTool]:
    return await load_mcp_tools(workspace_mcp_source, connection_name=connection_name)
