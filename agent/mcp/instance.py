"""Instance-wide MCP connections: the first tier every workspace and user inherits.

Before workspaces existed, the admin page's connections were this tier, stored
as flat ``["workspace_mcps"]`` records; they are adopted here on first read.
"""

import logging
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from agent.mcp import (
    MCPConnection,
    MCPConnectionUpdate,
    MCPSource,
    discover_tools,
    prepare_connection,
)
from agent.mcp.workspace import WORKSPACE_MCPS_NAMESPACE
from agent.store import TypedStore, delete_value, now_iso, search_all_entries

logger = logging.getLogger(__name__)

INSTANCE_MCPS_NAMESPACE = ["instance_mcps"]


def _store() -> TypedStore[MCPConnection]:
    return TypedStore(INSTANCE_MCPS_NAMESPACE, MCPConnection)


async def _adopt_pre_workspace_records() -> None:
    """Move the flat pre-workspaces records into the instance tier.

    A Store search matches by namespace prefix, so scanning the flat namespace
    can also surface records that live under a nested
    ``["workspace_mcps", <workspace>]`` namespace; only an entry whose own
    namespace is exactly the flat one (or one whose namespace the transport
    does not report, as with the in-memory test double, which only ever
    matches exactly) is a pre-workspaces record. Each adopted record is deleted
    from the flat namespace, so this has nothing left to do once it has run.
    """
    entries = await search_all_entries(WORKSPACE_MCPS_NAMESPACE)
    legacy = [entry for entry in entries if entry.namespace in (None, WORKSPACE_MCPS_NAMESPACE)]
    if not legacy:
        return
    target = _store()
    existing = {record.name for record in await target.search_all()}
    for entry in legacy:
        try:
            # Some flat records predate the revision/updated_at bookkeeping fields;
            # stamp fresh ones rather than reject a record that is otherwise sound.
            record = MCPConnection.model_validate(
                {"revision": uuid4().hex, "updated_at": now_iso(), **entry.value}
            )
        except ValidationError:
            logger.warning("Skipping unreadable pre-workspaces MCP record", exc_info=True)
            continue
        if record.name not in existing:
            await target.put(record.name, record)
        await delete_value(WORKSPACE_MCPS_NAMESPACE, record.name)


async def get_instance_mcp(name: str) -> MCPConnection | None:
    await _adopt_pre_workspace_records()
    return await _store().get(name)


async def list_instance_mcp_records() -> list[MCPConnection]:
    await _adopt_pre_workspace_records()
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
    # Adopt first, or the delete misses a record still in the flat namespace.
    await _adopt_pre_workspace_records()
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
