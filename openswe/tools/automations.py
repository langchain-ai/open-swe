"""Admin-thread tools for managing workspace automations."""

import logging
from typing import Any

from fastapi import HTTPException

from openswe.audit_logs.tools import audit_tool
from openswe.schedules import store as schedules
from openswe.tools.access import Policy, access, ack
from openswe.tools.admin_gate import configurable
from openswe.tools.mcp_exposure import expose_mcp

logger = logging.getLogger(__name__)

_READ = Policy(trusted="admin_thread", actor="admin")
_WRITE = Policy(trusted="admin_thread", actor="admin", sole=ack("automation.id"))


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


@expose_mcp(access="admin")
@access(_READ)
async def list_automations() -> dict[str, Any]:
    """Implement the `list_automations` tool."""
    return {"ok": True, "automations": await schedules.list_agent_schedules()}


@audit_tool()
@expose_mcp(access="admin")
@access(_WRITE)
async def create_automation(
    prompt: str,
    workspace: str,
    triggers: list[schedules.TriggerConfig],
    name: str | None = None,
    model_id: str | None = None,
    effort: str | None = None,
    admin_thread: bool = False,
) -> dict[str, Any]:
    """Implement the `create_automation` tool."""
    identity = await _identity()
    if identity is None:
        return {"ok": False, "error": "No GitHub identity is available for this admin thread."}
    login, email = identity
    try:
        record = await schedules.create_agent_schedule(
            login,
            schedules.ScheduleCreateBody(
                prompt=prompt,
                triggers=triggers,
                name=name,
                model_id=model_id,
                effort=effort,
                admin_thread=admin_thread,
                workspace=workspace,
            ),
            email=email,
            allow_admin_thread=True,
            use_workspace_credentials=configurable().source == "schedule",
        )
    except Exception as exc:
        return _error(exc)
    return {"ok": True, "automation": record}


@audit_tool()
@expose_mcp(access="admin")
@access(_WRITE)
async def update_automation(
    automation_id: str,
    prompt: str | None = None,
    triggers: list[schedules.TriggerConfig] | None = None,
    name: str | None = None,
    model_id: str | None = None,
    effort: str | None = None,
    enabled: bool | None = None,
    admin_thread: bool | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Implement the `update_automation` tool."""
    identity = await _identity()
    if identity is None:
        return {"ok": False, "error": "No GitHub identity is available for this admin thread."}
    values: dict[str, Any] = {
        "prompt": prompt,
        "triggers": triggers,
        "name": name,
        "model_id": model_id,
        "effort": effort,
        "enabled": enabled,
        "admin_thread": admin_thread,
        "workspace": workspace,
    }
    values = {key: value for key, value in values.items() if value is not None}
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


@audit_tool()
@expose_mcp(access="admin")
@access(_WRITE)
async def trigger_automation(automation_id: str) -> dict[str, Any]:
    """Implement the `trigger_automation` tool."""
    try:
        result = await schedules.trigger_agent_schedule(automation_id)
    except Exception as exc:
        return _error(exc)
    return {"ok": True, **result}


@audit_tool()
@expose_mcp(access="admin")
@access(_WRITE)
async def delete_automation(automation_id: str) -> dict[str, Any]:
    """Implement the `delete_automation` tool."""
    try:
        await schedules.delete_agent_schedule(automation_id)
    except Exception as exc:
        return _error(exc)
    return {"ok": True, "deleted": True}
