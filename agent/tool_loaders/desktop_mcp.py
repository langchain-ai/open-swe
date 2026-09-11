"""Desktop-owned local MCPs and session-bound cloud tool forwarding."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, cast

import httpx
from langchain_core.tools import BaseTool, ToolException
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import Connection

from agent.config import ENV
from agent.mcp import runtime

logger = logging.getLogger(__name__)


def _local_servers() -> dict[str, Any]:
    path = ENV.OPEN_SWE_LOCAL_MCPS_FILE.optional()
    if not path or not Path(path).exists():
        return {}
    data = json.loads(Path(path).read_text())
    servers = data["mcpServers"]
    if not isinstance(servers, dict):
        raise ValueError("mcpServers must be an object")
    return servers


async def _cloud_request(path: str, payload: dict[str, Any] | None = None) -> Any:
    url = ENV.OPEN_SWE_DESKTOP_MCP_URL.require()
    token = ENV.OPEN_SWE_DESKTOP_MCP_TOKEN.require()
    async with httpx.AsyncClient(timeout=65) as client:
        response = await client.request(
            "GET" if payload is None else "POST",
            f"{url}/{path}",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        response.raise_for_status()
        return response.json()


def _cloud_tool(login: str, backend: str, definition: dict[str, Any]) -> BaseTool:
    async def invoke(**arguments: Any) -> Any:
        try:
            local = await asyncio.to_thread(_local_servers)
            if definition["metadata"]["mcp_connection"] in local:
                raise ToolException("MCP scope changed; start a new run")
            return await _cloud_request(
                "call",
                {
                    "login": login,
                    "backend": backend,
                    "name": definition["name"],
                    "metadata": definition["metadata"],
                    "arguments": arguments,
                },
            )
        except Exception:
            raise ToolException(
                "Cloud MCP unavailable or account changed; start a new run"
            ) from None

    return runtime.MCPTool.from_function(
        coroutine=invoke,
        name=definition["name"],
        description=definition["description"],
        args_schema=definition["schema"],
        handle_tool_error=True,
    )


def _local_tool(name: str, settings: dict[str, Any], tool: BaseTool) -> BaseTool:
    async def invoke(**arguments: Any) -> Any:
        if (await asyncio.to_thread(_local_servers)).get(name) != settings:
            raise ToolException("Local MCP configuration changed; start a new run")
        return await tool.ainvoke(arguments)

    return runtime.MCPTool.from_function(
        coroutine=invoke,
        name=runtime.mcp_tool_name(name, tool.name),
        description=tool.description,
        args_schema=tool.args_schema,
        handle_tool_error=True,
    )


async def load_desktop_mcp_tools() -> list[BaseTool]:
    try:
        local = await asyncio.to_thread(_local_servers)
    except Exception:
        logger.warning("Local MCP configuration unavailable; omitting MCP tools")
        return []
    tools: list[BaseTool] = []
    try:
        catalog = await _cloud_request("catalog")
        tools.extend(
            _cloud_tool(catalog["login"], catalog["backend"], definition)
            for definition in catalog["tools"]
            if definition["metadata"]["mcp_connection"] not in local
        )
    except Exception:
        logger.warning("Cloud MCPs unavailable for desktop")
    for name, settings in local.items():
        if settings.get("enabled", True) is False:
            continue
        connection = {
            key: value for key, value in settings.items() if key not in {"enabled", "allowed_tools"}
        }
        connection.setdefault(
            "transport", "stdio" if "command" in connection else "streamable_http"
        )
        try:
            discovered = await asyncio.wait_for(
                MultiServerMCPClient({name: cast(Connection, connection)}).get_tools(), 30
            )
            for tool in discovered:
                if "allowed_tools" in settings and tool.name not in settings["allowed_tools"]:
                    continue
                tools.append(_local_tool(name, settings, tool))
        except Exception:
            logger.warning("Local MCP unavailable", extra={"mcp_name": name})
    return tools
