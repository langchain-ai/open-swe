"""Generic MCP discovery; execution requires an explicit workspace tool allowlist."""

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

import httpx
from langchain_core.tools import BaseTool, StructuredTool, ToolException
from langchain_mcp_adapters.interceptors import MCPToolCallRequest, MCPToolCallResult
from langchain_mcp_adapters.sessions import (
    Connection,
    SSEConnection,
    StreamableHttpConnection,
    create_session,
)
from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
from mcp.types import PaginatedRequestParams, Tool

from agent.dashboard.workspace_mcps import (
    WorkspaceMCP,
    WorkspaceMCPUpdate,
    get_workspace_mcp,
    list_workspace_mcp_records,
    prepare_workspace_mcp,
)
from agent.tool_loaders.mcp_transport import mcp_http_client
from agent.utils import ttl_cache

logger = logging.getLogger(__name__)
_TIMEOUT_SECONDS = 30


class _WorkspaceMCPTool(StructuredTool):
    """Forward remote properties without consuming LangChain's reserved keywords."""

    def _run(self, /, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("Workspace MCP tools require async invocation")

    async def _arun(self, /, *args: Any, **kwargs: Any) -> Any:
        if self.coroutine is None:
            raise ToolException("Workspace MCP tool has no async implementation")
        return await self.coroutine(*args, **kwargs)


def _connection(record: WorkspaceMCP) -> Connection:
    connection: SSEConnection | StreamableHttpConnection
    if record.transport == "sse":
        connection = {"transport": "sse", "url": record.url}
    else:
        connection = {"transport": "streamable_http", "url": record.url}
    connection["headers"] = record.connection_headers()
    connection["timeout"] = _TIMEOUT_SECONDS
    connection["sse_read_timeout"] = _TIMEOUT_SECONDS
    connection["httpx_client_factory"] = partial(mcp_http_client, record.url)
    return connection


async def _discover_tools(record: WorkspaceMCP) -> list[Tool]:
    async with create_session(_connection(record)) as session:
        await session.initialize()
        page = await session.list_tools()
        tools = list(page.tools)
        cursors: set[str] = set()
        while page.nextCursor:
            if page.nextCursor in cursors:
                raise ValueError("MCP server repeated a catalog cursor")
            cursors.add(page.nextCursor)
            page = await session.list_tools(params=PaginatedRequestParams(cursor=page.nextCursor))
            tools.extend(page.tools)
        if len({tool.name for tool in tools}) != len(tools):
            raise ValueError("MCP server returned duplicate tool names")
        return tools


def _discovery_error(error: Exception) -> str:
    pending: list[BaseException] = [error]
    while pending:
        current = pending.pop()
        if isinstance(current, BaseExceptionGroup):
            pending.extend(reversed(current.exceptions))
        elif isinstance(current, httpx.HTTPStatusError):
            status = current.response.status_code
            hint = {
                401: "Check the authentication headers",
                403: "Check credentials, permissions, the server region, and MCP access settings",
                404: "Check the MCP server URL",
                429: "Wait before retrying; the MCP server is rate limiting requests",
            }.get(status, "Check the MCP server availability")
            return f"MCP tool discovery failed (HTTP {status}). {hint}"
        elif isinstance(current, (TimeoutError, httpx.TimeoutException)):
            return "MCP tool discovery timed out; check the server and try again"
    return "Could not discover MCP tools; check the URL and authentication headers"


async def _discover_catalog(record: WorkspaceMCP) -> list[Tool]:
    try:
        return await asyncio.wait_for(_discover_tools(record), timeout=_TIMEOUT_SECONDS)
    except Exception as exc:
        # The shared cache logs refresh failures, so redact before handing errors to it.
        raise ValueError(_discovery_error(exc)) from None


async def discover_workspace_mcp(
    name: str, update: WorkspaceMCPUpdate | None = None
) -> list[dict[str, str]]:
    """List tool descriptions for an admin to choose; never execute any tools."""
    record = (
        await prepare_workspace_mcp(name, update)
        if update is not None
        else await get_workspace_mcp(name)
    )
    if record is None:
        raise ValueError("Workspace MCP connection does not exist")
    definitions = await _discover_catalog(record)
    return [{"name": tool.name, "description": tool.description or ""} for tool in definitions]


def _tool_name(connection_name: str, tool_name: str) -> str:
    full_name = f"mcp_{connection_name}_{tool_name}"
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", full_name)
    suffix = hashlib.sha256(json.dumps((connection_name, tool_name)).encode()).hexdigest()[:10]
    return f"{safe[:53]}_{suffix}"


def _wrap_tool(name: str, url: str, transport: str, definition: Tool) -> BaseTool:
    async def invoke(**arguments: Any) -> Any:
        try:
            record = await get_workspace_mcp(name)
            if record is None or not record.enabled:
                raise ToolException("Workspace MCP is disabled or disconnected")
            if record.url != url or record.transport != transport:
                raise ToolException("Workspace MCP connection changed; start a new run")
            if definition.name not in record.allowed_tools:
                raise ToolException("This tool is no longer allowed by the workspace MCP settings")

            async def forward_arguments(
                request: MCPToolCallRequest,
                handler: Callable[[MCPToolCallRequest], Awaitable[MCPToolCallResult]],
            ) -> MCPToolCallResult:
                # Preserve remote arguments named `runtime`, reserved by the adapter.
                return await handler(request.override(args=arguments))

            fresh = convert_mcp_tool_to_langchain_tool(
                None,
                definition,
                connection=_connection(record),
                tool_interceptors=[forward_arguments],
            )
            if not isinstance(fresh, StructuredTool) or fresh.coroutine is None:
                raise ToolException("Workspace MCP tool has no async implementation")
            return await asyncio.wait_for(fresh.coroutine(), timeout=_TIMEOUT_SECONDS)
        except ToolException:
            raise
        except Exception:
            logger.warning("Workspace MCP call failed", extra={"mcp_name": name})
            raise ToolException(
                "Workspace MCP call failed; check its connection and credentials"
            ) from None

    return _WorkspaceMCPTool.from_function(
        coroutine=invoke,
        name=_tool_name(name, definition.name),
        description=definition.description or definition.name,
        args_schema=definition.inputSchema,
        response_format="content_and_artifact",
        handle_tool_error=True,
    )


async def _load_tools(record: WorkspaceMCP) -> list[BaseTool]:
    try:
        definitions = await ttl_cache.cached(
            f"workspace-mcp:{record.name}:{record.revision}",
            600,
            partial(_discover_catalog, record),
        )
        return [
            _wrap_tool(record.name, record.url, record.transport, definition)
            for definition in definitions
            if definition.name in record.allowed_tools
        ]
    except Exception:
        logger.warning("Workspace MCP discovery failed", extra={"mcp_name": record.name})
        return []


async def load_workspace_mcp_tools() -> list[BaseTool]:
    try:
        records = await list_workspace_mcp_records()
    except Exception:
        logger.warning("Workspace MCP settings unavailable")
        return []
    groups = await asyncio.gather(
        *(_load_tools(record) for record in records if record.enabled and record.allowed_tools)
    )
    return [tool for group in groups for tool in group]
