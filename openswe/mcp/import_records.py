"""Move MCP connections from the LangGraph Store into PostgreSQL.

Runs through :func:`openswe.database.store_imports.run_store_import`, which
stops running it once a pass finds nothing left; a later release can delete
this module.

A Store record is copied, and deleted only once the copy has been in place for
a day: replicas of the previous release load these connections on every run
until their rollout finishes. A Store record saved after the copy, by such a
replica, replaces it. Records from before workspaces, flat under
``["workspace_mcps"]``, belong to the instance tier unless it has a record of
the same name.
"""

import logging
from collections.abc import Sequence
from uuid import uuid4

from pydantic import ValidationError

from openswe.database.store_imports import (
    StoreImport,
    may_delete_store_copy,
    store_copy_is_newer,
)
from openswe.mcp.models import MCPConnection
from openswe.mcp.records import (
    INSTANCE_CONNECTIONS,
    INSTANCE_MCPS_NAMESPACE,
    USER_MCPS_NAMESPACE,
    WORKSPACE_MCPS_NAMESPACE,
    MCPConnections,
    SharedMCPConnections,
    UserMCPConnections,
)
from openswe.store import delete_value, now_iso, search_all_entries
from openswe.users.records import UnknownUser
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)


async def delete_store_copy(connections: MCPConnections, name: str) -> None:
    """Delete a connection's Store records, so the import cannot bring back a deleted connection."""
    await delete_value(connections.namespace, name)
    if connections.namespace == INSTANCE_MCPS_NAMESPACE:
        await delete_value(WORKSPACE_MCPS_NAMESPACE, name)


async def import_store_mcp_connections() -> StoreImport:
    """Move every tier's connections out of the LangGraph Store."""
    result = StoreImport()
    instance_names: set[str] = set()
    for entry in await search_all_entries(INSTANCE_MCPS_NAMESPACE):
        if tuple(entry.namespace or INSTANCE_MCPS_NAMESPACE) == INSTANCE_MCPS_NAMESPACE:
            instance_names.add(entry.key)
            result += await _import(
                INSTANCE_CONNECTIONS, INSTANCE_MCPS_NAMESPACE, entry.key, entry.value
            )
    for entry in await search_all_entries(WORKSPACE_MCPS_NAMESPACE):
        match entry.namespace or list(WORKSPACE_MCPS_NAMESPACE):
            case [_]:
                value = None if entry.key in instance_names else entry.value
                result += await _import(
                    INSTANCE_CONNECTIONS, WORKSPACE_MCPS_NAMESPACE, entry.key, value
                )
            case [_, workspace] as namespace:
                result += await _import(
                    SharedMCPConnections("workspace", workspace), namespace, entry.key, entry.value
                )
    for entry in await search_all_entries(USER_MCPS_NAMESPACE):
        if entry.namespace is None or len(entry.namespace) != 2:
            continue
        login = entry.namespace[1]
        try:
            result += await _import(
                UserMCPConnections(login), entry.namespace, entry.key, entry.value
            )
        except UnknownUser:
            logger.warning(
                "Store MCP connection waits for its users row", extra={"github_login": login}
            )
            result += StoreImport(waiting=1)
    return result


async def _import(
    connections: MCPConnections,
    namespace: Sequence[str],
    key: str,
    value: JsonObject | None,
) -> StoreImport:
    """Copy a Store record saved after PostgreSQL's, and delete it once the copy has settled.

    ``value`` is ``None`` for a Store record that is never copied, only deleted.
    """
    record = None
    if value is not None:
        try:
            # Records from before workspaces may lack the bookkeeping fields.
            record = MCPConnection.model_validate(
                {"revision": uuid4().hex, "updated_at": now_iso(), **value}
            )
        except ValidationError:
            logger.warning(
                "Leaving an unreadable MCP connection in the Store",
                extra={"mcp_namespace": "/".join(namespace), "store_key": key},
                exc_info=True,
            )
            return StoreImport()
    written_at = await connections.written_at(key if record is None else record.name)
    if (
        record is not None
        and value is not None
        and (written_at is None or store_copy_is_newer(value, written_at))
    ):
        await connections.put(record)
        return StoreImport(moved=1)
    if not may_delete_store_copy(written_at):
        return StoreImport(waiting=1)
    await delete_value(namespace, key)
    return StoreImport(moved=1)
