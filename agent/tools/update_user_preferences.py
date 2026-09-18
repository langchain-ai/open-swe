"""Update the private thread owner's editable preferences."""

from typing import Literal

from pydantic import ValidationError

from agent.credential_scope import private_credential_login
from agent.dashboard.options import default_model_pair
from agent.dashboard.profiles import ProfileUpdate, get_profile, upsert_profile
from agent.dashboard.user_preferences import (
    UserPreferencesUpdate,
    get_user_preferences,
    set_user_preferences,
)


async def update_user_preferences(
    settings: dict[str, str | bool | None],
    category: Literal["profile", "dashboard"] = "profile",
) -> dict[str, str | bool]:
    """Update only explicitly supplied settings for the verified private owner."""
    login = await private_credential_login()
    if login is None:
        return {"ok": False, "error": "Preferences can only be changed in private threads"}
    schema = ProfileUpdate if category == "profile" else UserPreferencesUpdate
    unknown = settings.keys() - schema.model_fields.keys()
    if unknown:
        return {"ok": False, "error": f"Unknown preferences: {', '.join(sorted(unknown))}"}
    try:
        if category == "profile":
            current = (await get_profile(login)).model_dump(exclude_unset=True)
            model, effort = default_model_pair()
            update = ProfileUpdate.model_validate(
                {"default_model": model, "reasoning_effort": effort, **current, **settings}
            )
            update.validate_pairing()
            await upsert_profile(login, current.get("email", ""), update)
        else:
            preferences = await get_user_preferences(login)
            dashboard_update = UserPreferencesUpdate.model_validate({**preferences, **settings})
            await set_user_preferences(login, dashboard_update)
    except (ValidationError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "login": login}
