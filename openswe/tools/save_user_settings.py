"""Save ordinary settings for the requester."""

from langgraph.config import get_config

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.agent_overrides import resolve_github_login
from openswe.dashboard.personal_settings import SettingValue, patch_personal_settings
from openswe.tools.access import Policy, access, ack
from openswe.utils.json_types import as_json_object


@audit_tool()
@access(Policy(trusted="private", actor="owner", sole=ack(), direct=True))
async def save_user_settings(settings: dict[str, SettingValue]) -> dict[str, object]:
    """Implement the `save_user_settings` tool."""
    login = await resolve_github_login(as_json_object(get_config()))
    if not login:
        return {"ok": False, "error": "Could not resolve the requester's GitHub login"}
    if "concierge_mode" in settings:
        return {"ok": False, "error": "Concierge DMs are always enabled and cannot be toggled"}
    try:
        updated = await patch_personal_settings(login, settings)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "login": login, "updated": updated}
