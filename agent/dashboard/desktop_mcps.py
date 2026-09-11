"""Session-authorized cloud MCP execution for the desktop's private local runs."""

from typing import Any

from fastapi import HTTPException

from agent.dashboard.user_mcps import user_mcp_source
from agent.dashboard.workspace_mcps import workspace_mcp_source
from agent.mcp import load_mcp_tools


async def desktop_mcp_tools(login: str) -> list[Any]:
    return await load_mcp_tools(workspace_mcp_source, user_mcp_source(login))


async def desktop_mcp_catalog(login: str) -> dict[str, Any]:
    tools = await desktop_mcp_tools(login)
    return {
        "login": login,
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "schema": tool.args_schema
                if isinstance(tool.args_schema, dict)
                else tool.get_input_schema().model_json_schema(),
                "metadata": tool.metadata,
            }
            for tool in tools
        ],
    }


async def call_desktop_mcp(login: str, request: dict[str, Any]) -> Any:
    if request.get("login") != login:
        raise HTTPException(403, "Desktop account changed; start a new run")
    for tool in await desktop_mcp_tools(login):
        if tool.name == request.get("name") and tool.metadata == request.get("metadata"):
            return await tool.ainvoke(request.get("arguments", {}))
    raise HTTPException(409, "MCP connection changed or unavailable; start a new run")
