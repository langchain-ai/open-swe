"""MCP client helper for loading Logfire tools."""

import logging

from langchain_mcp_adapters.client import MultiServerMCPClient

from agent.utils.logfire import LOGFIRE_API_KEY, LOGFIRE_MCP_URL

logger = logging.getLogger(__name__)


async def get_logfire_mcp_tools() -> list:
    """Connect to Logfire's remote MCP server and return all available tools."""
    if not LOGFIRE_API_KEY:
        logger.debug("LOGFIRE_API_KEY not set, skipping Logfire MCP tools")
        return []

    try:
        client = MultiServerMCPClient(
            {
                "logfire": {
                    "transport": "http",
                    "url": LOGFIRE_MCP_URL,
                    "headers": {
                        "Authorization": f"Bearer {LOGFIRE_API_KEY}",
                    },
                },
            }
        )
        tools = await client.get_tools()
        logger.info("Loaded %d Logfire MCP tools", len(tools))
        return tools
    except Exception:
        logger.exception("Failed to connect to Logfire MCP server")
        return []
