"""Read safe settings for verified participants in the active thread."""

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from langgraph.config import get_config

from agent.credential_scope import private_credential_login
from agent.dashboard.personal_settings import PROFILE_SETTING_KEYS
from agent.dashboard.profiles import get_profile, normalize_profile_for_response
from agent.dashboard.user_credentials import get_notion_status
from agent.dashboard.user_instructions import get_user_instructions
from agent.dashboard.user_preferences import get_user_preferences
from agent.tools.access import Policy, access
from agent.users import User
from agent.utils.thread_participants import resolve_thread_participant_logins

logger = logging.getLogger(__name__)

_PROFILE_SETTING_KEYS = (
    "default_model",
    "reasoning_effort",
    "default_subagent_model",
    "subagent_reasoning_effort",
    "auto_fix_ci",
    "draft_prs",
    "review_draft_prs",
    "recent_thread_context_enabled",
)


def _safe_profile_settings(
    profile: dict[str, Any] | None, *, own_settings: bool = False
) -> dict[str, Any]:
    if not profile:
        return {}
    normalized = normalize_profile_for_response(profile)
    keys = PROFILE_SETTING_KEYS if own_settings else _PROFILE_SETTING_KEYS
    return {key: normalized[key] for key in keys if key in normalized}


async def _settings_for_login(login: str, *, own_settings: bool = False) -> dict[str, Any]:
    profile, instruction_record, notion = await asyncio.gather(
        get_profile(login),
        get_user_instructions(login),
        get_notion_status(login),
    )
    instructions = instruction_record.get("instructions") if instruction_record else ""
    profile_settings = _safe_profile_settings(profile, own_settings=own_settings)
    if own_settings:
        preferences = await User.preferences_for_login(login)
        profile_settings["concierge_mode"] = preferences.concierge_mode
    return {
        "login": login,
        "profile": profile_settings,
        "instructions": instructions if isinstance(instructions, str) else "",
        "connections": {
            "notion": notion.get("notion", {"connected": False}),
        },
    }


@access(Policy(trusted="private", actor="owner"))
async def read_user_settings() -> dict[str, Any]:
    """Implement the `read_user_settings` tool."""
    config = get_config()
    if not isinstance(config, Mapping):
        return {"success": False, "error": "Missing run config"}
    try:
        login = await private_credential_login(config)
    except Exception:
        logger.exception("Could not authorize personal settings read")
        return {"success": False, "error": "Could not verify the active thread requester"}
    if login:
        participant = await _settings_for_login(login, own_settings=True)
        participant["preferences"] = await get_user_preferences(login)
        return {
            "success": True,
            "participants": [participant],
            "unresolved_participant_count": 0,
        }
    logins, unresolved_count, error = await resolve_thread_participant_logins(config)
    if error or not logins:
        return {"success": False, "error": error or "No verified participants found"}
    participants = await asyncio.gather(*(_settings_for_login(login) for login in sorted(logins)))
    return {
        "success": True,
        "participants": list(participants),
        "unresolved_participant_count": unresolved_count,
    }
