"""Admin-managed MCP connections shared by one Open SWE workspace/deployment."""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from agent.mcp import MCPConnection, MCPConnectionUpdate, MCPSource, prepare_connection
from agent.store import TypedStore

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


class WorkspaceMCPRoute(APIRoute):
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
