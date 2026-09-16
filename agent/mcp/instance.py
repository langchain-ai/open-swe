"""Instance-wide MCP connections: the first tier every workspace and user inherits."""

from typing import Any

from agent.mcp import (
    MCPConnection,
    MCPConnectionUpdate,
    MCPSource,
    discover_tools,
    prepare_connection,
)
from agent.store import TypedStore

INSTANCE_MCPS_NAMESPACE = ["instance_mcps"]


def _store() -> TypedStore[MCPConnection]:
    return TypedStore(INSTANCE_MCPS_NAMESPACE, MCPConnection)


async def get_instance_mcp(name: str) -> MCPConnection | None:
    return await _store().get(name)


async def list_instance_mcp_records() -> list[MCPConnection]:
    return sorted(await _store().search_all(), key=lambda record: record.name)


async def list_instance_mcps() -> list[dict[str, Any]]:
    return [record.public() for record in await list_instance_mcp_records()]


async def prepare_instance_mcp(name: str, update: MCPConnectionUpdate) -> MCPConnection:
    """Validate a draft, reusing saved authentication only from the instance record."""
    if name != update.name:
        raise ValueError("Connection name must match its URL path")
    return await prepare_connection(update, await get_instance_mcp(name))


async def save_instance_mcp(name: str, update: MCPConnectionUpdate) -> dict[str, Any]:
    record = await prepare_instance_mcp(name, update)
    await _store().put(name, record)
    return record.public()


async def delete_instance_mcp(name: str) -> None:
    await _store().delete(name)


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
    definitions = await discover_tools(record, tuple(INSTANCE_MCPS_NAMESPACE))
    return [{"name": tool.name, "description": tool.description or ""} for tool in definitions]


def instance_mcp_source() -> MCPSource:
    return MCPSource(
        namespace=tuple(INSTANCE_MCPS_NAMESPACE),
        list_connections=list_instance_mcp_records,
        get_connection=get_instance_mcp,
    )
