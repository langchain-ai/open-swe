"""Personal MCP connections owned by one dashboard user and used only in their runs."""

from functools import partial
from typing import Any

from agent.mcp import (
    MCPConnection,
    MCPConnectionUpdate,
    MCPSource,
    discover_tools,
    prepare_connection,
)
from agent.mcp.store import MCPConnectionStore

USER_MCPS_NAMESPACE = ["user_mcps"]


def _store(login: str) -> MCPConnectionStore:
    return MCPConnectionStore("user", login)


async def get_user_mcp(login: str, name: str) -> MCPConnection | None:
    return await _store(login).get(name)


async def list_user_mcp_records(login: str) -> list[MCPConnection]:
    return sorted(await _store(login).list_all(), key=lambda record: record.name)


async def list_user_mcps(login: str) -> list[dict[str, Any]]:
    return [record.public() for record in await list_user_mcp_records(login)]


async def prepare_user_mcp(login: str, name: str, update: MCPConnectionUpdate) -> MCPConnection:
    """Validate a draft, reusing saved authentication only from the same user's record."""
    if name != update.name:
        raise ValueError("Connection name must match its URL path")
    return await prepare_connection(update, await get_user_mcp(login, name))


async def save_user_mcp(login: str, name: str, update: MCPConnectionUpdate) -> dict[str, Any]:
    record = await prepare_user_mcp(login, name, update)
    await _store(login).put(name, record)
    return record.public()


async def delete_user_mcp(login: str, name: str) -> None:
    await _store(login).delete(name)


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
    definitions = await discover_tools(record, (*USER_MCPS_NAMESPACE, _store(login).owner))
    return [{"name": tool.name, "description": tool.description or ""} for tool in definitions]


def user_mcp_source(login: str) -> MCPSource:
    login = login.strip().lower()
    return MCPSource(
        namespace=(*USER_MCPS_NAMESPACE, login),
        list_connections=partial(list_user_mcp_records, login),
        get_connection=partial(get_user_mcp, login),
    )
