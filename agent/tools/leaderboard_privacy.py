"""Admin-thread tools for the usage leaderboard identity policy."""

from typing import Any

from pydantic import BaseModel

from agent.dashboard.workspace_settings import (
    WorkspaceSettingsUpdate,
    get_instance_settings,
    patch_instance_settings,
)
from agent.dashboard.workspace_settings_cache import invalidate_settings_cache
from agent.tools.admin_gate import require_admin


class _PrivacyView(BaseModel):
    ok: bool
    usage_leaderboard_privacy_enabled: bool
    updated_at: str | None = None


async def get_usage_leaderboard_privacy() -> dict[str, Any]:
    """Read whether the usage leaderboard hides other members' identities from non-admins.

    When enabled, a non-admin sees every member except themself as an anonymous
    "Open SWE user" row with no login, email, avatar, or profile link; admins
    and each viewer's own row stay identified. When disabled, every signed-in
    user sees names, logins, and avatars; other members' emails are never shown.
    """
    if error := await require_admin("read the usage leaderboard privacy setting"):
        return {"ok": False, "error": error}
    settings = await get_instance_settings()
    view = _PrivacyView(
        ok=True,
        usage_leaderboard_privacy_enabled=settings.usage_leaderboard_privacy_enabled,
        updated_at=settings.get("updated_at")
        if isinstance(settings.get("updated_at"), str)
        else None,
    )
    return view.model_dump()


async def set_usage_leaderboard_privacy(enabled: bool) -> dict[str, Any]:
    """Set whether the usage leaderboard hides other members' identities from non-admins.

    This is an instance-level switch: it applies to every workspace and only
    changes its own field on the shared instance settings record.
    """
    if error := await require_admin("change the usage leaderboard privacy setting"):
        return {"ok": False, "error": error}
    record = await patch_instance_settings(
        WorkspaceSettingsUpdate(usage_leaderboard_privacy_enabled=enabled)
    )
    invalidate_settings_cache()
    updated_at = record.get("updated_at")
    return _PrivacyView(
        ok=True,
        usage_leaderboard_privacy_enabled=enabled,
        updated_at=updated_at if isinstance(updated_at, str) else None,
    ).model_dump()
