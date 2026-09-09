"""Lazy MCP tool groups for a run: one session per connection, credentials rechecked per call."""

import asyncio
import hashlib
import logging
import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from contextlib import AsyncExitStack
from typing import Any

import httpx
from langchain_core.tools import BaseTool, StructuredTool, ToolException
from langchain_mcp_adapters.interceptors import MCPToolCallRequest, MCPToolCallResult
from langchain_mcp_adapters.sessions import create_session
from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
from mcp import ClientSession
from mcp.types import PaginatedRequestParams, Tool

from agent.dashboard.mcp_connections import (
    WORKSPACE_OWNER,
    connection_config,
    get_record,
    list_records,
    runnable_tools,
)
from agent.dashboard.mcp_http import MCPConnectionError
from agent.middleware.dynamic_tools import IntegrationGroup

logger = logging.getLogger(__name__)
_TOOL_NAME_LIMIT = 64
_CALL_TIMEOUT = 300
_auth_failures: dict[tuple[str, str], tuple[Any, ...]] = {}


def prefixed_tool_name(server: str, tool: str, *, scope: str = "") -> str:
    """``mcp_<server>_<tool>_<digest>`` within provider name limits.

    The tool keeps up to 32 characters so the model still recognises it; the
    server label fills the rest. The digest covers ``scope`` (a connection id,
    or the server label) so equal names from different connections never clash.
    """
    digest = hashlib.sha256(f"{scope or server}\0{tool}".encode()).hexdigest()[:10]
    clean_tool = re.sub(r"[^a-zA-Z0-9_-]", "_", tool)[:32]
    budget = _TOOL_NAME_LIMIT - len("mcp_") - len(clean_tool) - len(digest) - 2
    clean_server = re.sub(r"[^a-zA-Z0-9_-]", "_", server).strip("_")[:budget]
    return f"mcp_{clean_server}_{clean_tool}_{digest}"


def desktop_tool_groups(tools: Sequence[BaseTool]) -> dict[str, Sequence[BaseTool]]:
    """Desktop tools arrive already named by ``agent.desktop_mcp``."""
    return {"Device MCP": list(tools)}


def group_name(name: str, connection_id: str, taken: Iterable[str]) -> str:
    """The connection's own name, suffixed only when another group already uses it."""
    candidate = name
    if candidate in taken:
        candidate = f"{name} ({connection_id[:8]})"
    return candidate


def _version(record: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(record.get(key) for key in ("updated_at", "tested_at", "oauth_configured"))


def _auth_failure(error: BaseException) -> bool:
    if isinstance(error, BaseExceptionGroup):
        return any(_auth_failure(item) for item in error.exceptions)
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in {401, 403}
    if isinstance(error, MCPConnectionError):
        return error.status_code in {401, 403, 409} or error.detail == (
            "OAuth endpoint rejected the request"
        )
    return False


class _Connection:
    """One saved connection for one run.

    The MCP handshake runs once per run, on ``stack``; every call after that
    reuses the session. Credentials, the enabled flag and the settings revision
    are still re-read from the store on each HTTP request by the session's auth
    flow, so an edit or a disconnect applies to the next call rather than the
    next run.
    """

    def __init__(self, owner: str, record: dict[str, Any], stack: AsyncExitStack | None) -> None:
        self.owner = owner
        self.id = record["id"]
        self.label = record["name"]
        self.version = _version(record)
        self.lock = asyncio.Lock()
        self._run_stack = stack
        self._sessions: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def _config(self) -> Any:
        key = (self.owner, self.id)
        if key in _auth_failures:
            record = await get_record(self.owner, self.id)
            if not record["enabled"]:
                raise MCPConnectionError(409, "MCP connection is unavailable")
            self.version = _version(record)
            if _auth_failures[key] == self.version:
                raise MCPConnectionError(409, "Reconnect this MCP connection before retrying")
            _auth_failures.pop(key, None)
        return await connection_config(self.owner, self.id)

    async def session(self) -> ClientSession | None:
        """The run's session, opened on first use; ``None`` when no run stack exists."""
        if self._run_stack is None:
            return None
        if self._session is None:
            sessions = AsyncExitStack()
            await self._run_stack.enter_async_context(sessions)
            session = await sessions.enter_async_context(create_session(await self._config()))
            await session.initialize()
            self._sessions, self._session = sessions, session
        return self._session

    async def reset(self) -> None:
        """Drop the session after a failure so the next call reconnects."""
        sessions, self._sessions, self._session = self._sessions, None, None
        if sessions is not None:
            try:
                await sessions.aclose()
            except Exception:
                logger.debug("MCP session close failed", extra={"connection_id": self.id})

    def failed(self, error: BaseException) -> None:
        if _auth_failure(error):
            if len(_auth_failures) >= 1024:
                _auth_failures.pop(next(iter(_auth_failures)))
            _auth_failures[(self.owner, self.id)] = self.version
        logger.warning("MCP connection unavailable", extra={"connection_id": self.id})

    async def _definitions(self) -> list[Tool]:
        session = await self.session()
        if session is None:
            async with create_session(await self._config()) as fresh:
                await fresh.initialize()
                return await _catalog(fresh)
        return await _catalog(session)

    async def _invoke(self, definition: Tool, arguments: dict[str, Any]) -> Any:
        async def forward_arguments(
            request: MCPToolCallRequest,
            handler: Callable[[MCPToolCallRequest], Awaitable[MCPToolCallResult]],
        ) -> MCPToolCallResult:
            # Preserve remote arguments named `runtime`, reserved by the adapter.
            return await handler(request.override(args=arguments))

        session = await self.session()
        fresh = convert_mcp_tool_to_langchain_tool(
            session,
            definition,
            connection=None if session is not None else await self._config(),
            tool_interceptors=[forward_arguments],
        )
        if not isinstance(fresh, StructuredTool) or fresh.coroutine is None:
            raise TypeError("MCP tool requires an async implementation")
        return await asyncio.wait_for(fresh.coroutine(), _CALL_TIMEOUT)

    def wrap(self, definition: Tool) -> BaseTool:
        async def call(**arguments: Any) -> Any:
            async with self.lock:
                try:
                    return await self._invoke(definition, arguments)
                except ToolException:
                    raise
                except Exception as exc:
                    await self.reset()
                    self.failed(exc)
                    raise ToolException(
                        "MCP connection unavailable. Reconnect it if authentication is required; "
                        "otherwise retry later."
                    ) from None

        return StructuredTool.from_function(
            coroutine=call,
            name=prefixed_tool_name(self.label, definition.name, scope=self.id),
            description=definition.description or definition.name,
            args_schema=definition.inputSchema,
            response_format="content_and_artifact",
            handle_tool_error=True,
        )

    async def load(self) -> Sequence[BaseTool]:
        async with self.lock:
            try:
                async with asyncio.timeout(45):
                    definitions = await self._definitions()
                return [self.wrap(definition) for definition in definitions]
            except Exception as exc:
                await self.reset()
                self.failed(exc)
                raise RuntimeError("MCP connection unavailable; retry later or reconnect") from None


async def _catalog(session: ClientSession) -> list[Tool]:
    tools: list[Tool] = []
    cursor = None
    cursors: set[str] = set()
    for _ in range(100):
        result = await session.list_tools(params=PaginatedRequestParams(cursor=cursor))
        tools.extend(result.tools)
        cursor = result.nextCursor
        if not cursor:
            return tools
        if cursor in cursors:
            raise MCPConnectionError(502, "Invalid MCP catalog pagination")
        cursors.add(cursor)
    raise MCPConnectionError(502, "MCP catalog exceeds the page limit")


async def load_mcp_groups(
    login: str | None,
    *,
    stack: AsyncExitStack | None = None,
    reserved_groups: Iterable[str] = (),
) -> dict[str, IntegrationGroup]:
    """Groups for the workspace's shared connections and the trusted login's own.

    ``login`` comes from the graph factory, never from the model. Only the tool
    names reach the model up front; the handshake waits for the first request.
    """
    records: list[tuple[str, dict[str, Any]]] = []
    for owner in (WORKSPACE_OWNER, login):
        if not owner:
            continue
        try:
            records.extend((owner, record) for record in await list_records(owner))
        except Exception:
            logger.warning("Unable to read MCP connection catalog", extra={"owner_scope": owner})
    groups: dict[str, IntegrationGroup] = {}
    taken = set(reserved_groups)
    for owner, record in records:
        if not record["enabled"]:
            continue
        names = runnable_tools(record)
        if not names:
            logger.info(
                "MCP connection has no runnable tools; test it or allow some",
                extra={"connection_id": record["id"]},
            )
            continue
        connection = _Connection(owner, record, stack)
        label = group_name(record["name"], record["id"], taken)
        taken.add(label)
        groups[label] = IntegrationGroup(
            tool_names=[
                prefixed_tool_name(connection.label, name, scope=connection.id) for name in names
            ],
            load=connection.load,
        )
    return groups
