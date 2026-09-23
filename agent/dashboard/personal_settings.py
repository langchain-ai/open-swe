"""Validated partial updates to ordinary server-backed personal settings."""

from collections.abc import Mapping

from agent.dashboard.options import default_model_pair
from agent.dashboard.profiles import (
    PROFILES_NAMESPACE,
    ProfileUpdate,
    get_profile,
    normalize_profile_for_response,
)
from agent.dashboard.user_preferences import (
    USER_PREFERENCES_NAMESPACE,
    UserPreferencesUpdate,
    set_user_preferences,
)
from agent.store import get_value, now_iso, put_value

PROFILE_SETTING_KEYS = frozenset(
    {
        "default_model",
        "reasoning_effort",
        "default_subagent_model",
        "subagent_reasoning_effort",
        "default_repo",
        "base_branch",
        "branch_prefix",
        "auto_fix_ci",
        "model_routing_enabled",
        "recent_thread_context_enabled",
        "dm_session_enabled",
        "draft_prs",
        "review_draft_prs",
    }
)
PREFERENCE_SETTING_KEYS = frozenset(
    {
        "default_visibility",
        "local_tracing_project",
        "default_workspace",
        "follow_up_behavior",
    }
)
type SettingValue = str | bool | None


async def patch_personal_settings(
    login: str, settings: Mapping[str, SettingValue]
) -> dict[str, object]:
    """Validate every requested change before writing either settings record."""
    if not settings:
        raise ValueError("Provide at least one personal setting to update")
    unknown = settings.keys() - PROFILE_SETTING_KEYS - PREFERENCE_SETTING_KEYS
    if unknown:
        raise ValueError(f"Unsupported personal settings: {', '.join(sorted(unknown))}")
    profile_patch = {key: value for key, value in settings.items() if key in PROFILE_SETTING_KEYS}
    preferences_patch = {
        key: value for key, value in settings.items() if key in PREFERENCE_SETTING_KEYS
    }
    profile = None
    preferences = None
    if profile_patch:
        profile = await get_profile(login) or {}
        model, effort = default_model_pair()
        merged = {
            "default_model": model,
            "reasoning_effort": effort,
            **{
                key: value
                for key, value in normalize_profile_for_response(profile).items()
                if key in PROFILE_SETTING_KEYS
            },
            **profile_patch,
        }
        update = ProfileUpdate.model_validate(merged)
        update.validate_pairing()
        validated = update.model_dump()
        profile_patch = {key: validated[key] for key in profile_patch}
        if "draft_prs" in profile_patch and profile_patch["draft_prs"] is None:
            profile_patch["draft_prs"] = profile.get("draft_prs", True)
        for model_key, effort_key in (
            ("default_model", "reasoning_effort"),
            ("default_subagent_model", "subagent_reasoning_effort"),
        ):
            if model_key in profile_patch or effort_key in profile_patch:
                for key in (model_key, effort_key):
                    if validated[key] != profile.get(key):
                        profile_patch[key] = validated[key]
    if preferences_patch:
        existing = await get_value(USER_PREFERENCES_NAMESPACE, login) or {}
        preferences = UserPreferencesUpdate.model_validate(
            {
                "default_visibility": "private",
                **{key: value for key, value in existing.items() if key in PREFERENCE_SETTING_KEYS},
                **preferences_patch,
            }
        )
    result: dict[str, object] = {}
    if profile is not None:
        await put_value(
            PROFILES_NAMESPACE,
            login,
            {**profile, **profile_patch, "login": login, "updated_at": now_iso()},
        )
        result.update(profile_patch)
    if preferences is not None:
        saved = await set_user_preferences(login, preferences)
        result.update({key: saved[key] for key in preferences_patch})
    return result
