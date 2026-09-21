"""Read the private thread owner's profile and dashboard preferences."""

from typing import TypedDict

from agent.credential_scope import private_credential_login
from agent.dashboard.profiles import get_profile
from agent.dashboard.user_preferences import get_user_preferences


class UserPreferencesResult(TypedDict, total=False):
    ok: bool
    error: str
    profile: dict[str, object]
    dashboard: dict[str, object]


async def read_user_preferences() -> UserPreferencesResult:
    """Read editable preferences for the verified private owner."""
    login = await private_credential_login()
    if login is None:
        return {"ok": False, "error": "Preferences can only be read in private threads"}
    profile = await get_profile(login)
    return {
        "ok": True,
        "profile": profile.model_dump(exclude={"login", "email", "updated_at"}),
        "dashboard": await get_user_preferences(login),
    }
