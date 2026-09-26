"""Save ordinary settings for the requester."""

from langgraph.config import get_config

from agent.dashboard.agent_overrides import resolve_github_login
from agent.dashboard.personal_settings import SettingValue, patch_personal_settings
from agent.tools.access import Policy, access, unchanged
from agent.utils.json_types import as_json_object


@access(Policy(trusted="private", actor="owner", sole=unchanged, direct=True))
async def save_user_settings(settings: dict[str, SettingValue]) -> dict[str, object]:
    """Implement the `save_user_settings` tool."""
    login = await resolve_github_login(as_json_object(get_config()))
    if not login:
        return {"ok": False, "error": "Could not resolve the requester's GitHub login"}
    if "concierge_mode" in settings:
        return {"ok": False, "error": "Change concierge_mode in the dashboard settings instead"}
    try:
        updated = await patch_personal_settings(login, settings)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "login": login, "updated": updated}
