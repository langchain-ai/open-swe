"""Admin-thread tools for managing workspace automations."""

import logging
from typing import Any

from fastapi import HTTPException

from agent.dashboard import schedules
from agent.tools.admin_gate import configurable, require_admin

logger = logging.getLogger(__name__)


async def _identity() -> tuple[str, str | None] | None:
    cfg = configurable()
    if cfg.source == "schedule":
        record = await schedules.authorized_admin_schedule(cfg)
        if not record:
            return None
        login = record.get("created_by") or f"system:schedule:{record['id']}"
        return login, record.get("user_email") or None
    if not cfg.github_login:
        return None
    return cfg.github_login, cfg.user_email or None


def _error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, HTTPException):
        return {"ok": False, "error": str(exc.detail)}
    logger.exception("Workspace automation operation failed")
    return {"ok": False, "error": str(exc)}


async def list_automations() -> dict[str, Any]:
    """Implement the `list_automations` tool."""
    if error := await require_admin("manage workspace automations"):
        return {"ok": False, "error": error}
    return {"ok": True, "automations": await schedules.list_agent_schedules()}


async def create_automation(
    prompt: str,
    schedule: str,
    name: str | None = None,
    repo: str | None = None,
    model_id: str | None = None,
    effort: str | None = None,
    slack_channel_id: str | None = None,
    slack_notification_mode: schedules.SlackNotificationMode = "always",
    admin_thread: bool = False,
) -> dict[str, Any]:
    """Implement the `create_automation` tool."""
    if error := await require_admin("manage workspace automations"):
        return {"ok": False, "error": error}
    identity = await _identity()
    if identity is None:
        return {"ok": False, "error": "No GitHub identity is available for this admin thread."}
    login, email = identity
    try:
        record = await schedules.create_agent_schedule(
            login,
            schedules.ScheduleCreateBody(
                prompt=prompt,
                schedule=schedule,
                name=name,
                repo=repo,
                model_id=model_id,
                effort=effort,
                slack_channel_id=slack_channel_id,
                slack_notification_mode=slack_notification_mode,
                admin_thread=admin_thread,
            ),
            email=email,
            allow_admin_thread=True,
            use_workspace_credentials=configurable().source == "schedule",
        )
    except Exception as exc:
        return _error(exc)
    return {"ok": True, "automation": record}


async def update_automation(
    automation_id: str,
    prompt: str | None = None,
    schedule: str | None = None,
    name: str | None = None,
    repo: str | None = None,
    clear_repo: bool = False,
    model_id: str | None = None,
    effort: str | None = None,
    enabled: bool | None = None,
    slack_channel_id: str | None = None,
    clear_slack_channel: bool = False,
    slack_notification_mode: schedules.SlackNotificationMode | None = None,
    admin_thread: bool | None = None,
) -> dict[str, Any]:
    """Implement the `update_automation` tool."""
    if error := await require_admin("manage workspace automations"):
        return {"ok": False, "error": error}
    identity = await _identity()
    if identity is None:
        return {"ok": False, "error": "No GitHub identity is available for this admin thread."}
    if clear_repo and repo is not None:
        return {"ok": False, "error": "clear_repo cannot be combined with repo"}
    if clear_slack_channel and slack_channel_id is not None:
        return {
            "ok": False,
            "error": "clear_slack_channel cannot be combined with slack_channel_id",
        }
    values: dict[str, Any] = {
        "prompt": prompt,
        "schedule": schedule,
        "name": name,
        "model_id": model_id,
        "effort": effort,
        "enabled": enabled,
        "slack_notification_mode": slack_notification_mode,
        "admin_thread": admin_thread,
    }
    values = {key: value for key, value in values.items() if value is not None}
    if repo is not None or clear_repo:
        values["repo"] = repo or ""
    if slack_channel_id is not None or clear_slack_channel:
        values["slack_channel_id"] = slack_channel_id
    try:
        record = await schedules.update_agent_schedule(
            automation_id,
            identity[0],
            schedules.ScheduleUpdateBody(**values),
            email=identity[1],
            allow_admin_thread=True,
            use_workspace_credentials=configurable().source == "schedule",
        )
    except Exception as exc:
        return _error(exc)
    return {"ok": True, "automation": record}


async def trigger_automation(automation_id: str) -> dict[str, Any]:
    """Implement the `trigger_automation` tool."""
    if error := await require_admin("manage workspace automations"):
        return {"ok": False, "error": error}
    try:
        result = await schedules.trigger_agent_schedule(automation_id)
    except Exception as exc:
        return _error(exc)
    return {"ok": True, **result}


async def delete_automation(automation_id: str) -> dict[str, Any]:
    """Implement the `delete_automation` tool."""
    if error := await require_admin("manage workspace automations"):
        return {"ok": False, "error": error}
    try:
        await schedules.delete_agent_schedule(automation_id)
    except Exception as exc:
        return _error(exc)
    return {"ok": True, "deleted": True}
