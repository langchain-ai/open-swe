"""Instance-wide MCP connections: the first tier every workspace and user inherits."""

from typing import Any

from openswe.mcp import (
    MCPConnection,
    MCPConnectionUpdate,
    MCPSource,
    discover_tools,
    prepare_connection,
)
from openswe.mcp.records import INSTANCE_CONNECTIONS


async def get_instance_mcp(name: str) -> MCPConnection | None:
    return await INSTANCE_CONNECTIONS.get(name)


async def list_instance_mcp_records() -> list[MCPConnection]:
    return await INSTANCE_CONNECTIONS.records()


async def list_instance_mcps() -> list[dict[str, Any]]:
    return [record.public() for record in await list_instance_mcp_records()]


async def prepare_instance_mcp(name: str, update: MCPConnectionUpdate) -> MCPConnection:
    """Validate a draft, reusing saved authentication only from the instance record."""
    if name != update.name:
        raise ValueError("Connection name must match its URL path")
    return await prepare_connection(update, await get_instance_mcp(name))


async def save_instance_mcp(name: str, update: MCPConnectionUpdate) -> dict[str, Any]:
    record = await prepare_instance_mcp(name, update)
    await INSTANCE_CONNECTIONS.put(record)
    return record.public()


async def delete_instance_mcp(name: str) -> None:
    await INSTANCE_CONNECTIONS.delete(name)


async def discover_instance_mcp(
    name: str, update: MCPConnectionUpdate | None = None
) -> list[dict[str, str]]:
    """List tool descriptions for an admin to choose; never execute any tools."""
    record = (
        await prepare_instance_mcp(name, update)
        if update is not None
        else await get_instance_mcp(name)
    )
    if record is None:
        raise ValueError("Instance MCP connection does not exist")
    definitions = await discover_tools(record, INSTANCE_CONNECTIONS.namespace)
    return [{"name": tool.name, "description": tool.description or ""} for tool in definitions]


def instance_mcp_source() -> MCPSource:
    return INSTANCE_CONNECTIONS.source()
