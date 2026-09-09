"""Personal-scope adapter for the shared MCP runtime."""

from agent.dashboard.user_mcps import get_user_mcp, prepare_user_mcp, user_mcp_source
from agent.mcp import MCPConnectionUpdate, discover_tools


async def discover_user_mcp(
    login: str, name: str, update: MCPConnectionUpdate | None = None
) -> list[dict[str, str]]:
    """List tool descriptions for the owner to choose; never execute any tools."""
    record = (
        await prepare_user_mcp(login, name, update)
        if update is not None
        else await get_user_mcp(login, name)
    )
    if record is None:
        raise ValueError("Personal MCP connection does not exist")
    definitions = await discover_tools(record, user_mcp_source(login).namespace)
    return [{"name": tool.name, "description": tool.description or ""} for tool in definitions]
