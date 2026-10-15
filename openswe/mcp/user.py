"""Personal MCP connections owned by one dashboard user and used only in their runs."""

from typing import Any

from openswe.credential_scope import private_credential_login
from openswe.mcp import (
    MCPConnection,
    MCPConnectionUpdate,
    MCPSource,
    discover_tools,
    prepare_connection,
)
from openswe.mcp.records import UserMCPConnections


async def get_user_mcp(login: str, name: str) -> MCPConnection | None:
    return await UserMCPConnections(login).get(name)


async def list_user_mcp_records(login: str) -> list[MCPConnection]:
    return await UserMCPConnections(login).records()


async def list_user_mcps(login: str) -> list[dict[str, Any]]:
    return [record.public() for record in await list_user_mcp_records(login)]


async def prepare_user_mcp(login: str, name: str, update: MCPConnectionUpdate) -> MCPConnection:
    """Validate a draft, reusing saved authentication only from the same user's record."""
    if name != update.name:
        raise ValueError("Connection name must match its URL path")
    return await prepare_connection(update, await get_user_mcp(login, name))


async def save_user_mcp(login: str, name: str, update: MCPConnectionUpdate) -> dict[str, Any]:
    record = await prepare_user_mcp(login, name, update)
    await UserMCPConnections(login).put(record)
    return record.public()


async def delete_user_mcp(login: str, name: str) -> None:
    await UserMCPConnections(login).delete(name)


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
    definitions = await discover_tools(record, UserMCPConnections(login).namespace)
    return [{"name": tool.name, "description": tool.description or ""} for tool in definitions]


def user_mcp_source(login: str) -> MCPSource:
    connections = UserMCPConnections(login)

    async def authorize() -> None:
        owner = await private_credential_login()
        if owner is None or owner.strip().lower() != connections.login:
            raise RuntimeError("Personal MCP tools require the private thread owner")

    return connections.source(authorize)
