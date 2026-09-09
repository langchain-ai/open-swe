"""Lazy owner-scoped MCP catalogs with credential revalidation on every call."""

import asyncio
import hashlib
import logging
import re
from collections.abc import Iterable, Sequence
from typing import Any, cast

import httpx
from langchain_core.tools import BaseTool, StructuredTool, ToolException
from langchain_core.utils.pydantic import model_json_schema
from langchain_mcp_adapters.sessions import Connection
from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool, load_mcp_tools
from mcp.types import Tool

from agent.dashboard.mcp_connections import connection_config, list_connections
from agent.dashboard.mcp_http import MCPConnectionError
from agent.middleware.dynamic_tools import IntegrationGroup

logger = logging.getLogger(__name__)
_auth_failures: dict[tuple[str, str], tuple[Any, ...]] = {}


_TOOL_NAME_LIMIT = 64


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
    def __init__(self, login: str, record: dict[str, Any]) -> None:
        self.login = login
        self.id = record["id"]
        self.label = record["name"]
        self.version = _version(record)
        self.lock = asyncio.Lock()

    async def config(self) -> Connection:
        key = (self.login, self.id)
        if key in _auth_failures:
            records = await list_connections(self.login)
            record = next((item for item in records if item["id"] == self.id), None)
            if record is None or not record["enabled"]:
                raise MCPConnectionError(409, "MCP connection is unavailable")
            self.version = _version(record)
            if _auth_failures[key] == self.version:
                raise MCPConnectionError(409, "Reconnect this MCP connection before retrying")
            _auth_failures.pop(key, None)
        return cast(Connection, await connection_config(self.login, self.id))

    def failed(self, error: BaseException) -> None:
        if _auth_failure(error):
            if len(_auth_failures) >= 1024:
                _auth_failures.pop(next(iter(_auth_failures)))
            _auth_failures[(self.login, self.id)] = self.version
        logger.warning("MCP connection unavailable", extra={"connection_id": self.id})

    def wrap(self, tool: BaseTool) -> BaseTool:
        schema = tool.args_schema if tool.args_schema is not None else tool.get_input_schema()
        definition = Tool(
            name=tool.name,
            description=tool.description,
            inputSchema=schema if isinstance(schema, dict) else model_json_schema(schema),
        )

        async def call(**arguments: Any) -> Any:
            async with self.lock:
                try:
                    config = await self.config()
                    fresh = cast(
                        StructuredTool,
                        convert_mcp_tool_to_langchain_tool(None, definition, connection=config),
                    )
                    if fresh.coroutine is None:
                        raise TypeError("MCP tool requires an async implementation")
                    return await fresh.coroutine(**arguments)
                except ToolException:
                    raise
                except Exception as exc:
                    self.failed(exc)
                    raise ToolException(
                        "MCP connection unavailable. Reconnect it if authentication is required; "
                        "otherwise retry later."
                    ) from None

        return StructuredTool.from_function(
            coroutine=call,
            name=prefixed_tool_name(self.label, tool.name, scope=self.id),
            description=tool.description,
            args_schema=schema,
            response_format="content_and_artifact",
            handle_tool_error=True,
        )

    async def load(self) -> Sequence[BaseTool]:
        async with self.lock:
            try:
                async with asyncio.timeout(45):
                    tools = await load_mcp_tools(None, connection=await self.config())
                return [self.wrap(tool) for tool in tools]
            except Exception as exc:
                self.failed(exc)
                raise RuntimeError("MCP connection unavailable; retry later or reconnect") from None


async def load_mcp_groups(
    login: str | None, *, reserved_groups: Iterable[str] = ()
) -> dict[str, IntegrationGroup]:
    """Use only the trusted triggering login supplied by the graph factory."""
    if not login:
        return {}
    try:
        records = await list_connections(login)
    except Exception:
        logger.warning("Unable to read MCP connection catalog")
        return {}
    groups: dict[str, IntegrationGroup] = {}
    taken = set(reserved_groups)
    for record in records:
        if not record["enabled"]:
            continue
        connection = _Connection(login, record)
        names = sorted(set(record.get("tool_names", [])))
        if not names:
            logger.warning(
                "MCP connection has no cached catalog; save or test it to discover tools",
                extra={"connection_id": record["id"]},
            )
            continue
        label = group_name(record["name"], record["id"], taken)
        taken.add(label)
        groups[label] = IntegrationGroup(
            tool_names=[
                prefixed_tool_name(connection.label, name, scope=connection.id) for name in names
            ],
            load=connection.load,
        )
    return groups
