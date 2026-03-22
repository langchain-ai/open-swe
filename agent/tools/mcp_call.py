import os
from typing import Any

import httpx


MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL")
MCP_SERVER_API_KEY = os.environ.get("MCP_SERVER_API_KEY")
MCP_SERVER_TIMEOUT = int(os.environ.get("MCP_SERVER_TIMEOUT", "30"))
MCP_SERVER_NAME = os.environ.get("MCP_SERVER_NAME", "mcp")


def mcp_call(tool_name: str, arguments: dict[str, Any]) -> str:
    """Call a tool on the configured MCP server.

    Sends a JSON-RPC 2.0 tools/call request to the MCP server configured
    via MCP_SERVER_URL. Authentication is handled automatically via
    MCP_SERVER_API_KEY.

    Use this tool to access external knowledge, context, or services
    exposed through MCP. Check AGENTS.md for available tool names
    and their arguments.

    Args:
        tool_name: Name of the MCP tool to call
        arguments: Dictionary of arguments for the tool

    Returns:
        The text content from the MCP server response.

    Raises:
        ValueError: If MCP_SERVER_URL is not configured
        httpx.HTTPStatusError: If the MCP server returns an HTTP error status
    """
    if not MCP_SERVER_URL:
        raise ValueError(
            "MCP_SERVER_URL environment variable is not set. "
            "Configure it to use the mcp_call tool."
        )

    headers = {"Content-Type": "application/json"}
    if MCP_SERVER_API_KEY:
        headers["Authorization"] = f"Bearer {MCP_SERVER_API_KEY}"

    response = httpx.post(
        MCP_SERVER_URL,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": {k: v for k, v in arguments.items() if v is not None},
            },
        },
        timeout=MCP_SERVER_TIMEOUT,
    )
    response.raise_for_status()

    result = response.json()
    if "error" in result:
        return f"MCP error: {result['error'].get('message', 'Unknown error')}"

    content = result.get("result", {}).get("content", [])
    if not content:
        return "No content returned from MCP server."

    return content[0].get("text", "")
