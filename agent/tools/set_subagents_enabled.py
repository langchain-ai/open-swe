"""Tool: update the triggering user's subagent preference."""

import logging

from langgraph.config import get_config

from agent.dashboard.agent_overrides import resolve_github_login
from agent.dashboard.profiles import set_disable_subagents
from agent.utils.json_types import as_json_object

logger = logging.getLogger(__name__)


async def set_subagents_enabled(enabled: bool) -> dict[str, str | bool]:
    """Implement the `set_subagents_enabled` tool."""
    login = await resolve_github_login(as_json_object(get_config()))
    if not login:
        return {"ok": False, "error": "Could not resolve the triggering user's GitHub login"}

    try:
        await set_disable_subagents(login, not enabled)
    except Exception as exc:
        logger.exception(
            "Failed to update subagent preference",
            extra={"github_login": login},
        )
        return {"ok": False, "error": f"failed to update subagent preference: {exc}"}

    return {"ok": True, "login": login, "subagents_enabled": enabled}
