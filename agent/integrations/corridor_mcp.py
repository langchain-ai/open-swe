"""Server-side Corridor tools backed by Corridor's hosted MCP server.

Credentials are read from environment variables and attached as an
``Authorization: Bearer ...`` header to the MCP connection, which runs in the
LangGraph server process. The sandbox never holds Corridor credentials.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

DEFAULT_CORRIDOR_MCP_URL = "https://app.corridor.dev/api/mcp"
_CORRIDOR_HOST = "app.corridor.dev"
_CORRIDOR_PATH = "/api/mcp"
_MCP_TIMEOUT_SECONDS = 30.0
_TOKEN_ENV_NAMES = (
    "CORRIDOR_API_TOKEN",
    "CORRIDOR_MCP_TOKEN",
    "CORRIDOR_TOKEN",
)
_TOKEN_QUERY_PARAMS = frozenset({"token", "api_key"})
_URL_ENV_NAMES = (
    "CORRIDOR_MCP_URL",
    "CORRIDOR_MCP_SERVER_URL",
)
_ALLOWED_TOOL_NAMES = frozenset({"analyzePlan"})


@dataclass(frozen=True)
class CorridorMCPConfig:
    url: str
    token: str


def _first_env_value(names: tuple[str, ...]) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _extract_token_from_query(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    token = ""
    for name in _TOKEN_QUERY_PARAMS:
        values = query.pop(name, [])
        if not token:
            token = next((value.strip() for value in values if value.strip()), "")
    cleaned_query = urlencode(query, doseq=True)
    cleaned_url = urlunparse(parsed._replace(query=cleaned_query))
    return token, cleaned_url


def _is_corridor_mcp_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == _CORRIDOR_HOST
        and parsed.path.rstrip("/") == _CORRIDOR_PATH
    )


def load_corridor_mcp_config() -> CorridorMCPConfig | None:
    """Return Corridor MCP config when the environment contains valid settings."""
    url = _first_env_value(_URL_ENV_NAMES) or DEFAULT_CORRIDOR_MCP_URL
    token = _first_env_value(_TOKEN_ENV_NAMES)
    query_token, url = _extract_token_from_query(url)
    if not token:
        token = query_token
    if not token:
        return None
    if not _is_corridor_mcp_url(url):
        logger.warning("Ignoring Corridor MCP config with non-Corridor URL: %s", url)
        return None
    return CorridorMCPConfig(url=url, token=token)


async def _build_mcp_tools(config: CorridorMCPConfig) -> list[BaseTool]:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {
            "corridor": {
                "transport": "http",
                "url": config.url,
                "headers": {
                    "Authorization": f"Bearer {config.token}",
                },
                "timeout": timedelta(seconds=_MCP_TIMEOUT_SECONDS),
            }
        }
    )
    return await client.get_tools()


async def load_corridor_tools() -> list[BaseTool]:
    """Return the allowed Corridor MCP tools when configured, else ``[]``.

    Failures (missing config, unreachable MCP server) degrade to an empty list so
    the agent still starts without Corridor tools.
    """
    config = load_corridor_mcp_config()
    if config is None:
        return []
    try:
        tools = await _build_mcp_tools(config)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to load Corridor MCP tools", exc_info=True)
        return []
    allowed_tools = [tool for tool in tools if tool.name in _ALLOWED_TOOL_NAMES]
    logger.info(
        "Loaded %d Corridor MCP tool(s), exposing %d allowed tool(s)",
        len(tools),
        len(allowed_tools),
    )
    return allowed_tools
