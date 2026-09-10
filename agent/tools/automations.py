"""Tools for requesting and managing workspace automations."""

import logging
from typing import Any, Literal

from fastapi import HTTPException

from agent.dashboard import automation_requests, schedules
from agent.dashboard.admin import is_admin
from agent.dashboard.profiles import get_profile
from agent.dashboard.user_mappings import slack_id_for_login
from agent.slack.client import (
    get_slack_channel_context,
    get_slack_user_info,
    post_slack_dm,
    slack_channel_allows_operations,
)
from agent.tools.admin_gate import configurable, require_admin

logger = logging.getLogger(__name__)


def _identity() -> tuple[str, str | None] | None:
    cfg = configurable()
    if not cfg.github_login:
        return None
    return cfg.github_login, cfg.user_email or None


async def _requester_name(login: str) -> str:
    cfg = configurable()
    slack_thread = cfg.slack_thread
    if slack_thread and slack_thread.triggering_user_name.strip():
        return slack_thread.triggering_user_name.strip()
    profile = await get_profile(login) or {}
    for key in ("name", "display_name"):
        value = profile.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    slack_user_id = (
        slack_thread.triggering_user_id.strip()
        if slack_thread and slack_thread.triggering_user_id.strip()
        else await slack_id_for_login(login)
    )
    if slack_user_id:
        user = await get_slack_user_info(slack_user_id)
        if user:
            user_profile = user.get("profile")
            if isinstance(user_profile, dict):
                for key in ("display_name", "real_name"):
                    value = user_profile.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
            value = user.get("real_name")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return login


async def _requester_slack_id(login: str) -> str | None:
    slack_thread = configurable().slack_thread
    if slack_thread and slack_thread.triggering_user_id.strip():
        return slack_thread.triggering_user_id.strip()
    return await slack_id_for_login(login)


async def _slack_channel_name(channel_id: str | None) -> str | None:
    if not channel_id:
        return None
    context = await get_slack_channel_context(channel_id)
    if not slack_channel_allows_operations(context):
        raise HTTPException(400, "Slack channel must be internal to this workspace")
    name = context.get("name") or context.get("name_normalized")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(400, "Slack channel name could not be resolved")
    return name.strip()


async def _notify_requester(record: dict[str, Any], message: str) -> None:
    request = await automation_requests.get_automation_request(str(record.get("id") or ""))
    slack_user_id = request.get("requester_slack_id") if request else None
    if not isinstance(slack_user_id, str) or not slack_user_id:
        logger.warning(
            "Automation request requester has no Slack ID", extra={"request_id": record.get("id")}
        )
        return
    message_ts, error = await post_slack_dm(slack_user_id, message)
    if not message_ts:
        logger.warning(
            "Failed to notify automation requester",
            extra={"request_id": record.get("id"), "slack_error": error},
        )


def _error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, HTTPException):
        return {"ok": False, "error": str(exc.detail)}
    logger.exception("Workspace automation operation failed")
    return {"ok": False, "error": str(exc)}


async def list_automations() -> dict[str, Any]:
    """Implement the `list_automations` tool."""
    if error := require_admin("manage workspace automations"):
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
    identity = _identity()
    if identity is None:
        return {"ok": False, "error": "No GitHub identity is available for this thread."}
    login, email = identity
    try:
        body = schedules.ScheduleCreateBody(
            prompt=prompt,
            schedule=schedule,
            name=name,
            repo=repo,
            model_id=model_id,
            effort=effort,
            slack_channel_id=slack_channel_id,
            slack_notification_mode=slack_notification_mode,
            admin_thread=admin_thread,
        )
        if is_admin(email, login=login):
            record = await schedules.create_agent_schedule(
                login,
                body,
                email=email,
                allow_admin_thread=True,
            )
            return {"ok": True, "automation": record}
        if admin_thread:
            return {
                "ok": False,
                "error": "Only workspace admins can request admin-thread automations.",
            }
        request = await automation_requests.create_automation_request(
            body,
            requester_login=login,
            requester_email=email,
            requester_name=await _requester_name(login),
            requester_slack_id=await _requester_slack_id(login),
            slack_channel_name=await _slack_channel_name(slack_channel_id),
        )
    except Exception as exc:
        return _error(exc)
    return {"ok": True, "requested": True, "request": request}


async def list_automation_requests(
    status: automation_requests.AutomationRequestStatus | Literal["all"] = "pending",
) -> dict[str, Any]:
    """Implement the `list_automation_requests` tool."""
    if error := require_admin("review automation requests"):
        return {"ok": False, "error": error}
    return {"ok": True, "requests": await automation_requests.list_automation_requests(status)}


async def approve_automation_request(request_id: str) -> dict[str, Any]:
    """Implement the `approve_automation_request` tool."""
    if error := require_admin("review automation requests"):
        return {"ok": False, "error": error}
    identity = _identity()
    if identity is None:
        return {"ok": False, "error": "No GitHub identity is available for this admin thread."}
    try:
        async with automation_requests.automation_request_lock(request_id):
            pending = await automation_requests.get_automation_request(request_id)
            if not pending:
                raise HTTPException(404, "automation request not found")
            if pending.get("status") != "pending":
                raise HTTPException(409, f"automation request is already {pending.get('status')}")
            requester_login = pending.get("requested_by")
            if not isinstance(requester_login, str) or not requester_login:
                raise HTTPException(409, "automation requester identity is unavailable")
            body = schedules.ScheduleCreateBody.model_validate(pending.get("automation"))
            created = await schedules.create_agent_schedule(
                requester_login,
                body,
                email=pending.get("requester_email"),
                allow_admin_thread=False,
            )
            try:
                resolved = await automation_requests.resolve_automation_request(
                    request_id,
                    status="approved",
                    resolved_by=identity[0],
                    automation_id=str(created.get("id") or "") or None,
                )
            except Exception:
                await schedules.delete_agent_schedule(str(created.get("id") or ""))
                raise
        await _notify_requester(
            resolved,
            f"Your Open SWE automation request *{created.get('name') or 'Scheduled agent'}* was approved and created.",
        )
    except Exception as exc:
        return _error(exc)
    return {"ok": True, "request": resolved, "automation": created}


async def deny_automation_request(
    request_id: str,
    message: str | None = None,
) -> dict[str, Any]:
    """Implement the `deny_automation_request` tool."""
    if error := require_admin("review automation requests"):
        return {"ok": False, "error": error}
    identity = _identity()
    if identity is None:
        return {"ok": False, "error": "No GitHub identity is available for this admin thread."}
    denial_message = message.strip() if isinstance(message, str) and message.strip() else None
    try:
        async with automation_requests.automation_request_lock(request_id):
            resolved = await automation_requests.resolve_automation_request(
                request_id,
                status="denied",
                resolved_by=identity[0],
                denial_message=denial_message,
            )
        name = resolved.get("automation", {}).get("name") or "Scheduled agent"
        detail = f" Message: {denial_message}" if denial_message else ""
        await _notify_requester(
            resolved,
            f"Your Open SWE automation request *{name}* was denied.{detail}",
        )
    except Exception as exc:
        return _error(exc)
    return {"ok": True, "request": resolved}


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
    if error := require_admin("manage workspace automations"):
        return {"ok": False, "error": error}
    identity = _identity()
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
        )
    except Exception as exc:
        return _error(exc)
    return {"ok": True, "automation": record}


async def trigger_automation(automation_id: str) -> dict[str, Any]:
    """Implement the `trigger_automation` tool."""
    if error := require_admin("manage workspace automations"):
        return {"ok": False, "error": error}
    try:
        result = await schedules.trigger_agent_schedule(automation_id)
    except Exception as exc:
        return _error(exc)
    return {"ok": True, **result}


async def delete_automation(automation_id: str) -> dict[str, Any]:
    """Implement the `delete_automation` tool."""
    if error := require_admin("manage workspace automations"):
        return {"ok": False, "error": error}
    try:
        await schedules.delete_agent_schedule(automation_id)
    except Exception as exc:
        return _error(exc)
    return {"ok": True, "deleted": True}
