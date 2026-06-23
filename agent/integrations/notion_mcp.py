"""Server-side Notion tools backed by Notion's hosted MCP server."""

from __future__ import annotations

import logging
from datetime import timedelta

from langchain_core.tools import BaseTool

from ..dashboard.notion_oauth import NOTION_MCP_URL
from ..dashboard.user_credentials import get_notion_access_token

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


async def load_notion_tools(login: str) -> list[BaseTool]:
    """Return Notion MCP tools for a connected user."""
    access_token = await get_notion_access_token(login)
    if not access_token:
        return []
    try:
        tools = await _build_mcp_tools(access_token)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to load Notion MCP tools", exc_info=True)
        return []
    logger.info("Loaded %d Notion MCP tool(s) for %s", len(tools), login)
    return tools
