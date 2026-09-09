"""Admin-managed MCP connections shared by one Open SWE workspace/deployment."""

from typing import Any

from agent.mcp import MCPConnection, MCPConnectionUpdate, MCPSource, prepare_connection
from agent.store import TypedStore

WORKSPACE_MCPS_NAMESPACE = ["workspace_mcps"]

_store = TypedStore(WORKSPACE_MCPS_NAMESPACE, MCPConnection)


async def get_workspace_mcp(name: str) -> MCPConnection | None:
    return await _store.get(name)


async def list_workspace_mcp_records() -> list[MCPConnection]:
    return sorted(await _store.search_all(), key=lambda record: record.name)


async def list_workspace_mcps() -> list[dict[str, Any]]:
    return [record.public() for record in await list_workspace_mcp_records()]


async def prepare_workspace_mcp(name: str, update: MCPConnectionUpdate) -> MCPConnection:
    """Validate a draft and resolve saved authentication without persisting it."""
    if name != update.name:
        raise ValueError("Connection name must match its URL path")
    return await prepare_connection(update, await get_workspace_mcp(name))


async def save_workspace_mcp(name: str, update: MCPConnectionUpdate) -> dict[str, Any]:
    record = await prepare_workspace_mcp(name, update)
    await _store.put(name, record)
    return record.public()


async def delete_workspace_mcp(name: str) -> None:
    await _store.delete(name)


workspace_mcp_source = MCPSource(
    namespace=tuple(WORKSPACE_MCPS_NAMESPACE),
    list_connections=list_workspace_mcp_records,
    get_connection=get_workspace_mcp,
)
