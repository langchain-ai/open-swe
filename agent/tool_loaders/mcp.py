"""Lazy owner-scoped MCP catalogs with credential revalidation on every call."""

import asyncio
import hashlib
import logging
import re
from collections.abc import Sequence
from typing import Any, cast

import httpx
from langchain_core.tools import BaseTool, StructuredTool, ToolException
from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool, load_mcp_tools
from mcp.types import Tool

from agent.dashboard.mcp_connections import connection_config, list_connections
from agent.dashboard.mcp_http import MCPConnectionError
from agent.middleware.dynamic_tools import IntegrationGroup

logger = logging.getLogger(__name__)
_auth_failures: dict[tuple[str, str], tuple[Any, ...]] = {}


def prefixed_tool_name(server: str, tool: str) -> str:
    digest = hashlib.sha256(f"{server}\0{tool}".encode()).hexdigest()[:20]
    clean_server = re.sub(r"[^a-zA-Z0-9_-]", "_", server)[:18]
    clean_tool = re.sub(r"[^a-zA-Z0-9_-]", "_", tool)[:20]
    return f"mcp_{clean_server}_{clean_tool}_{digest}"


def desktop_tool_groups(tools: Sequence[BaseTool]) -> dict[str, Sequence[BaseTool]]:
    return {
        "Device MCP": [
            tool.model_copy(update={"name": prefixed_tool_name("desktop", tool.name)})
            for tool in tools
        ]
    }


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
        self.server = f"{record['name']}_{self.id}"
        self.version = _version(record)
        self.lock = asyncio.Lock()

    async def config(self) -> dict[str, Any]:
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
        return await connection_config(self.login, self.id)

    def failed(self, error: BaseException) -> None:
        if _auth_failure(error):
            if len(_auth_failures) >= 1024:
                _auth_failures.pop(next(iter(_auth_failures)))
            _auth_failures[(self.login, self.id)] = self.version
        logger.warning("MCP connection unavailable", extra={"connection_id": self.id})

    def wrap(self, tool: BaseTool) -> BaseTool:
        schema = tool.args_schema
        definition = Tool(
            name=tool.name,
            description=tool.description,
            inputSchema=schema if isinstance(schema, dict) else schema.model_json_schema(),
        )

        async def call(**arguments: Any) -> Any:
            async with self.lock:
                try:
                    config = await self.config()
                    fresh = cast(
                        StructuredTool,
                        convert_mcp_tool_to_langchain_tool(None, definition, connection=config),
                    )
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
            name=prefixed_tool_name(self.server, tool.name),
            description=tool.description,
            args_schema=tool.args_schema,
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


async def load_mcp_groups(login: str | None) -> dict[str, IntegrationGroup]:
    """Use only the trusted triggering login supplied by the graph factory."""
    if not login:
        return {}
    try:
        records = await list_connections(login)
    except Exception:
        logger.warning("Unable to read MCP connection catalog")
        return {}
    groups = {}
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
        groups[f"MCP {record['id']}"] = IntegrationGroup(
            tool_names=[prefixed_tool_name(connection.server, name) for name in names],
            load=connection.load,
        )
    return groups
