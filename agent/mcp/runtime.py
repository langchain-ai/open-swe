"""MCP discovery and execution over ordered, caller-authorized connection sources."""

import asyncio
import hashlib
import json
import logging
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal, get_args

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

from agent.database import postgres
from agent.mcp.catalog import CATALOG_TTL, CatalogKey, MCPToolCatalog, catalog_key
from agent.mcp.models import MCPConnection
from agent.mcp.oauth import MCPOAuthError, connection_auth
from agent.mcp.transport import mcp_http_client
from agent.utils import ttl_cache
from agent.utils.startup_trace import apm_span
from mcp.types import PaginatedRequestParams, Tool

logger = logging.getLogger(__name__)
_TIMEOUT_SECONDS = 30

type CatalogOutcome = Literal["hit", "stale", "miss", "memory", "failed"]

_REFRESHES: dict[CatalogKey, asyncio.Task[None]] = {}


@dataclass(frozen=True)
class MCPSource:
    """An authorized scope with an owner-specific namespace for cache isolation.

    Callers enforce access before supplying a source. Lookups must raise on errors;
    only a genuinely absent connection may return None.
    """

    namespace: tuple[str, ...]
    list_connections: Callable[[], Awaitable[list[MCPConnection]]]
    get_connection: Callable[[str], Awaitable[MCPConnection | None]]


async def _resolve_connection(
    name: str, sources: tuple[MCPSource, ...]
) -> tuple[MCPSource, MCPConnection] | None:
    for source in reversed(sources):
        if record := await source.get_connection(name):
            return source, record
    return None


class _MCPTool(StructuredTool):
    """Forward remote properties without consuming LangChain's reserved keywords."""

    def _run(self, /, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("MCP tools require async invocation")

    async def _arun(self, /, *args: Any, **kwargs: Any) -> Any:
        if self.coroutine is None:
            raise ToolException("MCP tool has no async implementation")
        return await self.coroutine(*args, **kwargs)


def _connection(record: MCPConnection, namespace: tuple[str, ...]) -> Connection:
    connection: SSEConnection | StreamableHttpConnection
    if record.transport == "sse":
        connection = {"transport": "sse", "url": record.url}
    else:
        connection = {"transport": "streamable_http", "url": record.url}
    connection["headers"] = record.connection_headers()
    connection["timeout"] = _TIMEOUT_SECONDS
    connection["sse_read_timeout"] = _TIMEOUT_SECONDS
    connection["httpx_client_factory"] = partial(mcp_http_client, record.url)
    if auth := connection_auth(record, namespace):
        connection["auth"] = auth
    return connection


async def _discover_tools(record: MCPConnection, namespace: tuple[str, ...]) -> list[Tool]:
    async with create_session(_connection(record, namespace)) as session:
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
        elif isinstance(current, MCPOAuthError):
            return str(current)
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


async def discover_tools(record: MCPConnection, namespace: tuple[str, ...]) -> list[Tool]:
    try:
        return await asyncio.wait_for(_discover_tools(record, namespace), timeout=_TIMEOUT_SECONDS)
    except Exception as exc:
        # The shared cache logs refresh failures, so redact before handing errors to it.
        raise ValueError(_discovery_error(exc)) from None


def _tool_name(connection_name: str, tool_name: str) -> str:
    full_name = f"mcp_{connection_name}_{tool_name}"
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", full_name)
    suffix = hashlib.sha256(json.dumps((connection_name, tool_name)).encode()).hexdigest()[:10]
    return f"{safe[:53]}_{suffix}"


def _wrap_tool(
    name: str,
    url: str,
    transport: str,
    definition: Tool,
    namespace: tuple[str, ...],
    sources: tuple[MCPSource, ...],
) -> BaseTool:
    async def invoke(**arguments: Any) -> Any:
        try:
            resolved = await _resolve_connection(name, sources)
            if resolved is None:
                raise ToolException("MCP is disabled or disconnected")
            source, record = resolved
            if source.namespace != namespace:
                raise ToolException("MCP connection scope changed; start a new run")
            if not record.enabled:
                raise ToolException("MCP is disabled or disconnected")
            if record.url != url or record.transport != transport:
                raise ToolException("MCP connection changed; start a new run")
            if definition.name not in record.allowed_tools:
                raise ToolException("This tool is no longer allowed by the MCP connection settings")

            async def forward_arguments(
                request: MCPToolCallRequest,
                handler: Callable[[MCPToolCallRequest], Awaitable[MCPToolCallResult]],
            ) -> MCPToolCallResult:
                # Preserve remote arguments named `runtime`, reserved by the adapter.
                return await handler(request.override(args=arguments))

            fresh = convert_mcp_tool_to_langchain_tool(
                None,
                definition,
                connection=_connection(record, namespace),
                tool_interceptors=[forward_arguments],
            )
            if not isinstance(fresh, StructuredTool) or fresh.coroutine is None:
                raise ToolException("MCP tool has no async implementation")
            return await asyncio.wait_for(fresh.coroutine(), timeout=_TIMEOUT_SECONDS)
        except ToolException:
            raise
        except Exception:
            logger.warning("MCP call failed", extra={"mcp_name": name})
            raise ToolException("MCP call failed; check its connection and credentials") from None

    return _MCPTool.from_function(
        coroutine=invoke,
        name=_tool_name(name, definition.name),
        description=definition.description or definition.name,
        args_schema=definition.inputSchema,
        response_format="content_and_artifact",
        handle_tool_error=True,
        metadata={"mcp_tool_name": definition.name},
    )


async def _discover_and_store(
    record: MCPConnection, namespace: tuple[str, ...], *, background: bool
) -> list[Tool]:
    with apm_span("mcp.discover", {"mcp.name": record.name, "mcp.background": background}):
        definitions = await discover_tools(record, namespace)
    try:
        await MCPToolCatalog.save(
            catalog_key(namespace, record.name),
            record.revision,
            definitions,
            refresh=background,
        )
    except Exception:
        logger.warning("MCP catalog save failed", extra={"mcp_name": record.name}, exc_info=True)
    return definitions


async def _refresh(record: MCPConnection, namespace: tuple[str, ...]) -> None:
    try:
        await _discover_and_store(record, namespace, background=True)
    except Exception as exc:
        # discover_tools already redacted the message.
        logger.warning(
            "MCP catalog refresh failed", extra={"mcp_name": record.name, "mcp_error": str(exc)}
        )


def _schedule_refresh(record: MCPConnection, namespace: tuple[str, ...]) -> None:
    key = catalog_key(namespace, record.name)
    running = _REFRESHES.get(key)
    if running is not None and not running.done():
        return
    task = asyncio.create_task(_refresh(record, namespace))
    _REFRESHES[key] = task
    task.add_done_callback(lambda _: _REFRESHES.pop(key, None))


async def _definitions(
    source: MCPSource, record: MCPConnection, stored: MCPToolCatalog | None
) -> tuple[list[Tool], CatalogOutcome]:
    if stored is not None and stored.revision == record.revision:
        if stored.stale:
            _schedule_refresh(record, source.namespace)
            return stored.definitions(), "stale"
        return stored.definitions(), "hit"
    return await _discover_and_store(record, source.namespace, background=False), "miss"


async def _cached_definitions(source: MCPSource, record: MCPConnection) -> list[Tool]:
    return await ttl_cache.cached_stale_while_revalidate(
        "mcp:" + json.dumps((source.namespace, record.name, record.revision)),
        CATALOG_TTL.total_seconds(),
        partial(discover_tools, record, source.namespace),
    )


async def _load_tools(
    source: MCPSource,
    record: MCPConnection,
    sources: tuple[MCPSource, ...],
    stored: dict[CatalogKey, MCPToolCatalog] | None,
) -> tuple[list[BaseTool], CatalogOutcome]:
    try:
        if stored is None:
            definitions, outcome = await _cached_definitions(source, record), "memory"
        else:
            definitions, outcome = await _definitions(
                source, record, stored.get(catalog_key(source.namespace, record.name))
            )
        tools = [
            _wrap_tool(
                record.name, record.url, record.transport, definition, source.namespace, sources
            )
            for definition in definitions
            if definition.name in record.allowed_tools
        ]
        return tools, outcome
    except Exception:
        logger.warning("MCP discovery failed", extra={"mcp_name": record.name})
        return [], "failed"


async def _stored_catalogs(
    connections: list[tuple[MCPSource, MCPConnection]],
) -> dict[CatalogKey, MCPToolCatalog] | None:
    """Stored catalogs for these connections, or None to fall back to the process cache."""
    if not postgres.configured():
        return None
    try:
        return await MCPToolCatalog.load_all(
            [catalog_key(source.namespace, record.name) for source, record in connections]
        )
    except Exception:
        logger.warning("MCP catalog read failed", exc_info=True)
        return None


async def load_mcp_tools(*sources: MCPSource, connection_name: str | None = None) -> list[BaseTool]:
    """Combine sources in precedence order; later connections replace earlier names entirely."""
    try:
        catalogs = await asyncio.gather(*(source.list_connections() for source in sources))
        resolved = {
            record.name: (source, record)
            for source, records in zip(sources, catalogs, strict=True)
            for record in records
        }
    except Exception:
        # Missing scope data must not silently expose a lower-precedence connection.
        logger.warning("MCP settings unavailable")
        return []
    connections = [
        (source, record)
        for _, (source, record) in sorted(resolved.items())
        if record.enabled
        and record.allowed_tools
        and (connection_name is None or record.name == connection_name)
    ]
    stored = await _stored_catalogs(connections)
    groups = await asyncio.gather(
        *(_load_tools(source, record, sources, stored) for source, record in connections)
    )
    outcomes = Counter(outcome for _, outcome in groups)
    logger.info(
        "MCP catalogs resolved",
        extra={
            f"mcp_catalog_{outcome}": outcomes[outcome]
            for outcome in get_args(CatalogOutcome.__value__)
        },
    )
    return [tool for tools, _ in groups for tool in tools]
