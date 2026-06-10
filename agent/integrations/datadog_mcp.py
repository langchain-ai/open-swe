"""Server-side Datadog tools backed by Datadog's hosted MCP server.

Credentials live in team settings (encrypted at rest) and are attached as
``DD_API_KEY`` / ``DD_APPLICATION_KEY`` headers to the MCP connection, which runs
in the LangGraph server process. The sandbox never holds Datadog credentials.

The default toolset is ``core`` (query-oriented: logs, metrics, traces,
dashboards, monitors, incidents, hosts, services, events). Override via
``DATADOG_MCP_TOOLSETS``.
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta

from langchain_core.tools import BaseTool

from ..dashboard.team_credentials import DatadogCredentials, get_datadog_credentials

logger = logging.getLogger(__name__)

DEFAULT_DATADOG_TOOLSETS = "core"
_MCP_TIMEOUT_SECONDS = 30.0


def _toolsets() -> str:
    return os.environ.get("DATADOG_MCP_TOOLSETS", DEFAULT_DATADOG_TOOLSETS).strip() or (
        DEFAULT_DATADOG_TOOLSETS
    )


async def _build_mcp_tools(creds: DatadogCredentials) -> list[BaseTool]:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {
            "datadog": {
                "transport": "streamable_http",
                "url": creds.mcp_url(_toolsets()),
                "headers": {
                    "DD_API_KEY": creds.api_key,
                    "DD_APPLICATION_KEY": creds.app_key,
                },
                "timeout": timedelta(seconds=_MCP_TIMEOUT_SECONDS),
            }
        }
    )
    return await client.get_tools()


async def load_datadog_tools() -> list[BaseTool]:
    """Return Datadog MCP tools when the team has connected Datadog, else ``[]``.

    Failures (no credentials, unreachable MCP server) degrade to an empty list so
    the agent still starts without Datadog tools.
    """
    creds = await get_datadog_credentials()
    if creds is None:
        return []
    try:
        tools = await _build_mcp_tools(creds)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to load Datadog MCP tools", exc_info=True)
        return []
    logger.info("Loaded %d Datadog MCP tool(s) (toolsets=%s)", len(tools), _toolsets())
    return tools
