"""Server-side Notion tools backed by Notion's hosted MCP server."""

import logging
from datetime import timedelta
from typing import Any, cast

from langchain_core.tools import BaseTool

from agent.dashboard.notion_oauth import NOTION_MCP_URL
from agent.dashboard.user_credentials import get_notion_access_token
from agent.utils.thread_participants import resolve_participant

logger = logging.getLogger(__name__)

_MCP_TIMEOUT_SECONDS = 30.0


async def _build_mcp_tools(access_token: str) -> list[BaseTool]:
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
    """Return Notion MCP tool definitions, keyed to no particular user.

    ``login`` only supplies a token to read the hosted server's tool list; the
    resulting definitions are identical for every user, so the agent's tool
    schema does not change when a different person replies in a thread. Each
    call names the participant to act for and resolves that person's token then.
    """
    access_token = await get_notion_access_token(login)
    if not access_token:
        return []
    try:
        tools = await _build_mcp_tools(access_token)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to load Notion MCP tools", exc_info=True)
        return []
    logger.info("Loaded %d Notion MCP tool definition(s)", len(tools))
    return [_refreshing_tool(tool) for tool in tools]
