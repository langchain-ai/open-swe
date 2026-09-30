"""Save ordinary settings for the authenticated private-thread requester."""

import logging

from langgraph.config import get_config

from agent.credential_scope import private_credential_login
from agent.dashboard.personal_settings import SettingValue, patch_personal_settings
from agent.run_config import RunConfig

logger = logging.getLogger(__name__)


def personal_settings_run_allowed(cfg: RunConfig) -> bool:
    """Exclude automatic and non-personal entry points from settings writes."""
    return (
        cfg.source in ("dashboard", "slack")
        and not cfg.background_task_completion
        and not cfg.schedule_id
        and not cfg.watch_key
    )


async def save_user_settings(settings: dict[str, SettingValue]) -> dict[str, object]:
    """Implement the `save_user_settings` tool."""
    config = get_config()
    if not personal_settings_run_allowed(RunConfig.from_config(config)):
        return {"ok": False, "error": "Personal settings require a direct private user run"}
    try:
        login = await private_credential_login(config)
    except Exception:
        logger.exception("Could not authorize personal settings update")
        return {"ok": False, "error": "Could not verify the private thread requester"}
    if not login:
        return {"ok": False, "error": "Personal settings require an authenticated private thread"}
    if "concierge_mode" in settings:
        return {"ok": False, "error": "Change concierge_mode in the dashboard settings instead"}
    try:
        updated = await patch_personal_settings(login, settings)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "login": login, "updated": updated}
