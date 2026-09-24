"""Server-side Notion tools backed by Notion's hosted MCP server."""

import asyncio
import logging
from datetime import timedelta
from typing import Any, cast

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.sessions import StreamableHttpConnection, create_session
from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
from mcp.types import PaginatedRequestParams, Tool
from pydantic import TypeAdapter

from agent.credential_scope import private_credential_login
from agent.dashboard.notion_oauth import NOTION_MCP_URL
from agent.dashboard.user_credentials import get_notion_access_token
from agent.utils import shared_cache
from agent.utils.thread_participants import resolve_participant

logger = logging.getLogger(__name__)

_MCP_TIMEOUT_SECONDS = 30.0


async def _build_mcp_tools(access_token: str) -> list[BaseTool]:
    connection: StreamableHttpConnection = {
        "transport": "streamable_http",
        "url": NOTION_MCP_URL,
        "headers": {"Authorization": f"Bearer {access_token}"},
        "timeout": timedelta(seconds=_MCP_TIMEOUT_SECONDS),
    }

    async def discover() -> list[Tool]:
        async with asyncio.timeout(_MCP_TIMEOUT_SECONDS), create_session(connection) as session:
            await session.initialize()
            page = await session.list_tools()
            definitions = list(page.tools)
            cursors: set[str] = set()
            while page.nextCursor:
                if page.nextCursor in cursors:
                    raise ValueError("Notion MCP repeated a catalog cursor")
                cursors.add(page.nextCursor)
                page = await session.list_tools(
                    params=PaginatedRequestParams(cursor=page.nextCursor)
                )
                definitions.extend(page.tools)
            return definitions

    definitions = await shared_cache.cached(
        shared_cache.scoped_key("notion:definitions", NOTION_MCP_URL, access_token),
        300,
        discover,
        adapter=TypeAdapter(list[Tool]),
        max_age=3600,
    )
    return [
        convert_mcp_tool_to_langchain_tool(None, definition, connection=connection)
        for definition in definitions
    ]


async def _fresh_mcp_tool(login: str, tool_name: str) -> BaseTool:
    owner = await private_credential_login()
    if owner is None or owner.lower() != login.strip().lower():
        raise RuntimeError("Personal Notion MCP tools require the private thread owner")
    access_token = await get_notion_access_token(owner)
    if not access_token:
        raise RuntimeError(
            "Notion MCP authorization unavailable; reconnect Notion in Profile Settings"
        )
    tools = await _build_mcp_tools(access_token)
    for tool in tools:
        if tool.name == tool_name:
            return tool
    raise RuntimeError(f"Notion MCP tool {tool_name!r} is no longer available")


def _tool_input(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str | dict[str, Any]:
    if args and kwargs:
        raise TypeError("Notion MCP tool received both positional and keyword input")
    if not args:
        return kwargs
    if len(args) == 1 and isinstance(args[0], str):
        return args[0]
    if len(args) == 1 and isinstance(args[0], dict):
        return args[0]
    raise TypeError("Notion MCP tool received invalid positional input")


class _RefreshingNotionMCPTool(BaseTool):
    mcp_tool_name: str

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Notion MCP tools must be called asynchronously")

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        payload = _tool_input(args, kwargs)
        if not isinstance(payload, dict):
            raise TypeError("Notion MCP tools require keyword input including on_behalf_of")
        on_behalf_of = payload.pop("on_behalf_of", "")
        login = await resolve_participant(str(on_behalf_of))
        tool = await _fresh_mcp_tool(login, self.mcp_tool_name)
        return await tool.ainvoke(payload)


def _with_on_behalf_of(args_schema: Any) -> dict[str, Any]:
    """Add the required participant argument to an MCP tool's input schema."""
    schema: dict[str, Any] = (
        dict(cast("dict[str, Any]", args_schema))
        if isinstance(args_schema, dict)
        else args_schema.model_json_schema()
    )
    properties: dict[str, Any] = dict(schema.get("properties") or {})
    properties["on_behalf_of"] = {
        "type": "string",
        "description": (
            "GitHub login of the thread participant whose Notion connection to use. "
            "Must be someone who has spoken in this thread."
        ),
    }
    schema["properties"] = properties
    required: list[Any] = list(schema.get("required") or [])
    required.append("on_behalf_of")
    schema["required"] = required
    return schema


def _refreshing_tool(tool: BaseTool) -> BaseTool:
    return _RefreshingNotionMCPTool(
        name=tool.name,
        description=tool.description,
        args_schema=_with_on_behalf_of(tool.args_schema),
        response_format="content",
        mcp_tool_name=tool.name,
    )


async def load_notion_tools(login: str) -> list[BaseTool]:
    """Load the private owner's personal Notion tools."""
    owner = await private_credential_login()
    if owner is None or owner.lower() != login.strip().lower():
        return []
    access_token = await get_notion_access_token(owner)
    if not access_token:
        return []
    try:
        tools = await _build_mcp_tools(access_token)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to load Notion MCP tools", exc_info=True)
        return []
    logger.info("Loaded %d Notion MCP tool definition(s)", len(tools))
    return [_refreshing_tool(tool) for tool in tools]
