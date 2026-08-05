"""Server-side Notion tools backed by Notion's hosted MCP server."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from langchain_core.tools import BaseTool

from ..dashboard.notion_oauth import NOTION_MCP_URL
from ..dashboard.user_credentials import get_notion_access_token

logger = logging.getLogger(__name__)

_MCP_TIMEOUT_SECONDS = 30.0


async def _build_mcp_tools(access_token: str) -> list[BaseTool]:
    """Build and return the available Notion MCP tools.

    A new MCP client is created using the provided access token and queried
    for the tools currently exposed by Notion's hosted MCP server.
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {
            "notion": {
                "transport": "streamable_http",
                "url": NOTION_MCP_URL,
                "headers": {
                    "Authorization": f"Bearer {access_token}",
                },
                "timeout": timedelta(seconds=_MCP_TIMEOUT_SECONDS),
            }
        }
    )
    return await client.get_tools()


async def _fresh_mcp_tool(login: str, tool_name: str) -> BaseTool:
    """Return the latest instance of a specific Notion MCP tool.

    A fresh tool instance is retrieved on every invocation to ensure it
    reflects the user's current credentials and the latest tool metadata.
    """
    access_token = await get_notion_access_token(login)
    if not access_token:
        raise RuntimeError(
            "Notion MCP authorization unavailable; reconnect Notion in Profile Settings"
        )

    tools = await _build_mcp_tools(access_token)

    for tool in tools:
        if tool.name == tool_name:
            return tool

    raise RuntimeError(f"Notion MCP tool {tool_name!r} is no longer available")


def _tool_input(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> str | dict[str, Any]:
    """Normalize positional or keyword arguments into MCP tool input.

    Accepts exactly one of the following:

    - A single string positional argument.
    - A single dictionary positional argument.
    - Keyword arguments.

    Raises:
        TypeError: If both positional and keyword arguments are supplied,
            or if the positional arguments are invalid.
    """
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
    """Proxy around a Notion MCP tool that refreshes credentials before use."""

    login: str
    mcp_tool_name: str

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        """Synchronous execution is not supported."""
        raise RuntimeError("Notion MCP tools must be called asynchronously")

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        """Refresh the underlying MCP tool and invoke it asynchronously."""
        tool = await _fresh_mcp_tool(self.login, self.mcp_tool_name)
        return await tool.ainvoke(_tool_input(args, kwargs))


def _refreshing_tool(login: str, tool: BaseTool) -> BaseTool:
    """Wrap an MCP tool so credentials are refreshed before each invocation."""
    return _RefreshingNotionMCPTool(
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
        response_format="content",
        login=login,
        mcp_tool_name=tool.name,
    )


async def load_notion_tools(login: str) -> list[BaseTool]:
    """Return refreshable Notion MCP tools for a connected user.

    If the user has not connected Notion or the MCP tools cannot be loaded,
    an empty list is returned.
    """
    access_token = await get_notion_access_token(login)

    if not access_token:
        return []

    try:
        tools = await _build_mcp_tools(access_token)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to load Notion MCP tools", exc_info=True)
        return []

    logger.info("Loaded %d Notion MCP tool(s) for %s", len(tools), login)

    return [_refreshing_tool(login, tool) for tool in tools]
