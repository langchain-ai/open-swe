"""Accept the hook events a local coding agent forwards over MCP."""

import logging

from pydantic import JsonValue

from openswe.tools.admin_gate import configurable
from openswe.tools.mcp_exposure import expose_mcp

logger = logging.getLogger(__name__)


@expose_mcp()
async def record_hook_event(event: dict[str, JsonValue]) -> str:
    """Implement the `record_hook_event` tool."""
    logger.debug(
        "Coding agent hook event received",
        extra={"github_login": configurable().github_login, "hook_event": event},
    )
    return ""
