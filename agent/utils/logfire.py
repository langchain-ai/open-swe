"""Logfire MCP configuration constants."""

import os

LOGFIRE_API_KEY = os.environ.get("LOGFIRE_API_KEY", "")
LOGFIRE_MCP_URL = os.environ.get(
    "LOGFIRE_MCP_URL",
    "https://logfire-us.pydantic.dev/mcp",
)
