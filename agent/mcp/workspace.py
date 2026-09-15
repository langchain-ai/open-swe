"""Admin-managed MCP connections, sharded by workspace slug."""

import logging
from collections.abc import Callable, Coroutine
from functools import partial
from typing import Any
from uuid import uuid4

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError

from agent.mcp import MCPConnection, MCPConnectionUpdate, MCPSource, prepare_connection
from agent.store import TypedStore, delete_value, now_iso, search_all_entries
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG

logger = logging.getLogger(__name__)

WORKSPACE_MCPS_NAMESPACE = ["workspace_mcps"]
_VALIDATION_MESSAGES = {
    "name": (
        "Connection name must start with a lowercase letter and contain only lowercase "
        "letters, numbers, hyphens, or underscores (1-32 characters); for example, incident"
    ),
    "url": (
        "Server URL must be HTTPS, at most 2048 characters, and contain no credentials, "
        "whitespace, or fragments; put authentication in headers"
    ),
    "transport": "Transport must be Streamable HTTP or SSE",
    "enabled": "Enabled must be true or false",
    "headers": (
        "Headers must have valid, unique names and plain-text values without line breaks; "
        "use at most 20 headers and 8192 characters per value"
    ),
    "allowed_tools": "Allowed tools must be a list of non-empty names (1-128 characters)",
    "oauth": "OAuth requires an HTTPS token URL, client ID, and client secret; use the client_credentials grant",
}


class MCPRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def redacted_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                # Error messages and nested locations can include submitted credentials.
                messages = dict.fromkeys(
                    _VALIDATION_MESSAGES.get(
                        error["loc"][1] if len(error["loc"]) > 1 else "",
                        "Invalid MCP connection settings",
                    )
                    for error in exc.errors()
                )
                return JSONResponse(status_code=422, content={"detail": "; ".join(messages)})

        return redacted_handler


def _store(workspace: str) -> TypedStore[MCPConnection]:
    return TypedStore([*WORKSPACE_MCPS_NAMESPACE, workspace.strip().lower()], MCPConnection)


async def _migrate_legacy_default() -> None:
    """Flat records predate workspaces and were always the default workspace's.

    A Store search matches by namespace prefix, so scanning the flat namespace
    can also surface records that actually live under a nested
    ``["workspace_mcps", <workspace>]`` namespace; only an entry whose own
    namespace is exactly the flat one (or one whose namespace the transport
    does not report, as with the in-memory test double, which only ever
    matches exactly) is legacy. Each migrated record is deleted from the flat
    namespace, so this has nothing left to migrate once it has run.
    """
    entries = await search_all_entries(WORKSPACE_MCPS_NAMESPACE)
    legacy = [entry for entry in entries if entry.namespace in (None, WORKSPACE_MCPS_NAMESPACE)]
    if not legacy:
        return
    target = _store(DEFAULT_WORKSPACE_SLUG)
    existing = {record.name for record in await target.search_all()}
    for entry in legacy:
        try:
            # Some flat records predate the revision/updated_at bookkeeping fields;
            # stamp fresh ones rather than reject a record that is otherwise sound.
            record = MCPConnection.model_validate(
                {"revision": uuid4().hex, "updated_at": now_iso(), **entry.value}
            )
        except ValidationError:
            logger.warning("Skipping unreadable legacy workspace MCP record", exc_info=True)
            continue
        if record.name not in existing:
            await target.put(record.name, record)
        await delete_value(WORKSPACE_MCPS_NAMESPACE, record.name)


async def get_workspace_mcp(workspace: str, name: str) -> MCPConnection | None:
    if workspace == DEFAULT_WORKSPACE_SLUG:
        await _migrate_legacy_default()
    return await _store(workspace).get(name)


async def list_workspace_mcp_records(workspace: str) -> list[MCPConnection]:
    if workspace == DEFAULT_WORKSPACE_SLUG:
        await _migrate_legacy_default()
    return sorted(await _store(workspace).search_all(), key=lambda record: record.name)


async def list_workspace_mcps(workspace: str) -> list[dict[str, Any]]:
    return [record.public() for record in await list_workspace_mcp_records(workspace)]


async def prepare_workspace_mcp(
    workspace: str, name: str, update: MCPConnectionUpdate
) -> MCPConnection:
    """Validate a draft and resolve saved authentication without persisting it."""
    if name != update.name:
        raise ValueError("Connection name must match its URL path")
    return await prepare_connection(update, await get_workspace_mcp(workspace, name))


async def save_workspace_mcp(
    workspace: str, name: str, update: MCPConnectionUpdate
) -> dict[str, Any]:
    record = await prepare_workspace_mcp(workspace, name, update)
    await _store(workspace).put(name, record)
    return record.public()


async def delete_workspace_mcp(workspace: str, name: str) -> None:
    await _store(workspace).delete(name)


def workspace_mcp_source(workspace: str) -> MCPSource:
    workspace = workspace.strip().lower()
    return MCPSource(
        namespace=(*WORKSPACE_MCPS_NAMESPACE, workspace),
        list_connections=partial(list_workspace_mcp_records, workspace),
        get_connection=partial(get_workspace_mcp, workspace),
    )
