"""Read safe settings for verified participants in the active thread."""

import asyncio
from collections.abc import Mapping
from typing import Any

from langgraph.config import get_config

from agent.dashboard.profiles import get_profile, normalize_profile_for_response
from agent.dashboard.user_credentials import get_notion_status
from agent.dashboard.user_instructions import get_user_instructions
from agent.dashboard.workspace_settings_cache import cached_workspace_settings
from agent.run_config import RunConfig
from agent.tools.admin_gate import actor_is_admin
from agent.utils.thread_participants import resolve_thread_participant_logins

_PROFILE_SETTING_KEYS = (
    "default_model",
    "reasoning_effort",
    "default_subagent_model",
    "subagent_reasoning_effort",
    "auto_fix_ci",
    "dm_session_enabled",
    "draft_prs",
    "review_draft_prs",
)
_WORKSPACE_SETTING_KEYS = (
    "review_draft_prs",
    "pr_summaries",
    "review_trace_links",
    "model_routing_enabled",
    "gateway_enabled",
    "fable_enabled",
    "expedited_review_enabled",
)


def _safe_profile_settings(profile: dict[str, Any] | None) -> dict[str, Any]:
    if not profile:
        return {}
    normalized = normalize_profile_for_response(profile)
    return {key: normalized[key] for key in _PROFILE_SETTING_KEYS if key in normalized}


async def _settings_for_login(login: str) -> dict[str, Any]:
    profile, instruction_record, notion = await asyncio.gather(
        get_profile(login),
        get_user_instructions(login),
        get_notion_status(login),
    )
    instructions = instruction_record.get("instructions") if instruction_record else ""
    return {
        "login": login,
        "profile": _safe_profile_settings(profile),
        "instructions": instructions if isinstance(instructions, str) else "",
        "connections": {
            "notion": notion.get("notion", {"connected": False}),
        },
    }


async def read_user_settings() -> dict[str, Any]:
    """Read verified participant settings and workspace flags for admins."""
    config = get_config()
    if not isinstance(config, Mapping):
        return {"success": False, "error": "Missing run config"}
    logins, unresolved_count, error = await resolve_thread_participant_logins(config)
    if error or not logins:
        return {"success": False, "error": error or "No verified participants found"}
    participants = await asyncio.gather(*(_settings_for_login(login) for login in sorted(logins)))
    result: dict[str, Any] = {
        "success": True,
        "participants": list(participants),
        "unresolved_participant_count": unresolved_count,
    }
    run_config = RunConfig.from_config(config)
    if await actor_is_admin(run_config, login=run_config.github_login):
        workspace_settings = await cached_workspace_settings(run_config.workspace_slug)
        result["workspace"] = {key: workspace_settings.get(key) for key in _WORKSPACE_SETTING_KEYS}
    return result
