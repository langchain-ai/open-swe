"""Threads whose MCP tools are hidden from the model and reachable only through the sandbox."""

import logging

from langgraph_sdk.client import LangGraphClient

from agent.users import User
from agent.utils import ttl_cache
from agent.utils.json_types import thread_metadata

logger = logging.getLogger(__name__)

MCP_TOOLS_IN_SANDBOX_KEY = "mcp_tools_in_sandbox"
# Fixed at creation, so a cached read never goes stale.
_CACHE_TTL_SECONDS = 3600


async def owner_wants_mcp_tools_in_sandbox(owner_login: str) -> bool:
    try:
        preferences = await User.preferences_for_login(owner_login)
    except Exception:
        logger.warning(
            "Could not load MCP tools mode preference",
            exc_info=True,
            extra={"owner_login": owner_login},
        )
        return False
    return preferences.mcp_tools_in_sandbox


async def thread_has_mcp_tools_in_sandbox(client: LangGraphClient, thread_id: str) -> bool:
    async def _load() -> bool:
        thread = await client.threads.get(thread_id=thread_id)
        return thread_metadata(thread).get(MCP_TOOLS_IN_SANDBOX_KEY) is True

    try:
        return await ttl_cache.cached(
            f"mcp-tools-in-sandbox:{thread_id}", _CACHE_TTL_SECONDS, _load
        )
    except Exception:
        logger.warning(
            "Could not read MCP tools mode for thread",
            exc_info=True,
            extra={"thread_id": thread_id},
        )
        return False
