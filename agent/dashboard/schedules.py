"""The ``/schedules`` HTTP surface: owner-scoped CRUD over recurring agent runs.

What a schedule *is*, and what firing one does, lives in
:mod:`agent.scheduling.agent_schedules`; this module only turns a signed-in
user's request into a stored record plus its scheduler cron.
"""

import logging
import uuid
from typing import Any

from fastapi import HTTPException

from ..config import langgraph_client
from ..scheduling.agent_schedules import (
    DEFAULT_SLACK_NOTIFICATION_MODE,
    SCHEDULE_RUN_STATE_NAMESPACE,
    SCHEDULE_TASK,
    SCHEDULES_NAMESPACE,
    SchedulePayload,
    get_agent_schedule,
    get_run_state,
    launch_agent_schedule_record,
    put_agent_schedule,
    repo_full_name,
    slack_notification_mode,
)
from ..scheduling.crons import delete_scheduler_crons, ensure_scheduler_cron
from ..settings.github_tokens import get_valid_access_token
from ..settings.options import normalize_model_choice
from ..settings.profiles import get_profile
from ..store import delete_value, now_iso, search_all_values
from ..utils.run_metadata import resolve_run_email
from .repo_access import repo_config_for_user
from .schedule_models import ScheduleCreateBody, ScheduleUpdateBody

logger = logging.getLogger(__name__)


def _derive_name(prompt: str) -> str:
    return prompt.strip().splitlines()[0][:80] or "Scheduled agent"


def _schedule_summary(
    record: dict[str, Any], run_state: dict[str, Any] | None = None
) -> dict[str, Any]:
    repo = record.get("repo") if isinstance(record.get("repo"), dict) else None
    state = run_state or record
    return {
        "id": record.get("id"),
        "name": record.get("name"),
        "prompt": record.get("prompt"),
        "schedule": record.get("schedule"),
        "repo": repo_full_name(repo),
        "slackChannelId": record.get("slack_channel_id"),
        "slackNotificationMode": slack_notification_mode(record),
        "model": record.get("model"),
        "effort": record.get("effort"),
        "enabled": bool(record.get("enabled")),
        "cronId": record.get("cron_id"),
        "lastThreadId": state.get("last_thread_id"),
        "lastRunId": state.get("last_run_id"),
        "lastTriggeredAt": state.get("last_triggered_at"),
        "lastError": state.get("last_error"),
        "lastErrorAt": state.get("last_error_at"),
        "createdAt": record.get("created_at"),
        "updatedAt": record.get("updated_at"),
    }


def _user_owns_schedule(record: dict[str, Any], login: str, email: str | None = None) -> bool:
    if record.get("created_by") == login:
        return True
    record_email = record.get("user_email")
    return bool(email and isinstance(record_email, str) and record_email == email.strip().lower())


def _assert_schedule_owner(
    record: dict[str, Any] | None, login: str, email: str | None = None
) -> None:
    if not record or not _user_owns_schedule(record, login, email):
        raise HTTPException(404, "schedule not found")


async def list_agent_schedules(login: str, *, email: str | None = None) -> list[dict[str, Any]]:
    searches: list[dict[str, Any]] = [{"created_by": login}]
    if email and email.strip():
        searches.append({"user_email": email.strip().lower()})

    seen: dict[str, dict[str, Any]] = {}
    run_states: dict[str, dict[str, Any]] = {}
    for filter in searches:
        for record in await search_all_values(SCHEDULES_NAMESPACE, filter=filter):
            schedule_id = record.get("id")
            if isinstance(schedule_id, str) and _user_owns_schedule(record, login, email):
                seen[schedule_id] = record
        for state in await search_all_values(SCHEDULE_RUN_STATE_NAMESPACE, filter=filter):
            schedule_id = state.get("schedule_id")
            if isinstance(schedule_id, str):
                run_states[schedule_id] = state
    records = list(seen.values())
    records.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return [_schedule_summary(record, run_states.get(record["id"])) for record in records]


async def _ensure_dashboard_github_token(login: str) -> None:
    token = await get_valid_access_token(login)
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")


async def _register_cron(record: dict[str, Any]) -> str:
    payload: SchedulePayload = {"schedule_id": record["id"]}
    return await ensure_scheduler_cron(
        langgraph_client(),
        kind=SCHEDULE_TASK,
        key=record["id"],
        schedule=record["schedule"],
        payload=payload,
    )


async def _retire_cron(record: dict[str, Any]) -> bool:
    # The record's own cron id reaches a cron the metadata search cannot: one
    # registered before schedules moved to the uniform ``(kind, key)`` metadata.
    cron_id = record.get("cron_id")
    return await delete_scheduler_crons(
        langgraph_client(),
        kind=SCHEDULE_TASK,
        key=record["id"],
        cron_id=cron_id if isinstance(cron_id, str) else None,
    )


async def create_agent_schedule(
    login: str, body: ScheduleCreateBody, *, email: str | None = None
) -> dict[str, Any]:
    await _ensure_dashboard_github_token(login)
    profile = await get_profile(login) or {}
    chosen_model, chosen_effort = normalize_model_choice(body.model_id, body.effort)
    repo = await repo_config_for_user(login, body.repo)
    schedule_id = str(uuid.uuid4())
    now = now_iso()
    record: dict[str, Any] = {
        "id": schedule_id,
        "name": (body.name or _derive_name(body.prompt)).strip(),
        "prompt": body.prompt.strip(),
        "schedule": body.schedule,
        "repo": repo,
        "slack_channel_id": body.slack_channel_id,
        "slack_notification_mode": body.slack_notification_mode,
        "model": chosen_model or profile.get("default_model") or "Default",
        "effort": chosen_effort or profile.get("reasoning_effort"),
        "base_branch": profile.get("base_branch") or "main",
        "branch_prefix": profile.get("branch_prefix"),
        "enabled": True,
        "cron_id": None,
        "last_thread_id": None,
        "last_run_id": None,
        "last_triggered_at": None,
        "last_error": None,
        "last_error_at": None,
        "created_by": login,
        "user_email": (await resolve_run_email(login, profile) or email or "").strip().lower(),
        "created_at": now,
        "updated_at": now,
    }
    await put_agent_schedule(record)
    try:
        cron_id = await _register_cron(record)
    except Exception as exc:
        await delete_value(SCHEDULES_NAMESPACE, schedule_id)
        logger.exception("Failed to create schedule cron for %s", schedule_id)
        raise HTTPException(502, "failed to create schedule cron") from exc
    record = await put_agent_schedule({**record, "cron_id": cron_id})
    return _schedule_summary(record)


async def update_agent_schedule(
    schedule_id: str, login: str, body: ScheduleUpdateBody, *, email: str | None = None
) -> dict[str, Any]:
    existing = await get_agent_schedule(schedule_id)
    _assert_schedule_owner(existing, login, email)
    assert existing is not None

    patch: dict[str, Any] = {}
    if body.prompt is not None:
        patch["prompt"] = body.prompt.strip()
    if body.schedule is not None:
        patch["schedule"] = body.schedule
    if body.name is not None:
        patch["name"] = body.name.strip() or _derive_name(patch.get("prompt", existing["prompt"]))
    if body.repo is not None:
        patch["repo"] = await repo_config_for_user(login, body.repo)
    if body.model_id is not None or body.effort is not None:
        model, effort = normalize_model_choice(body.model_id, body.effort)
        if model and effort:
            patch["model"] = model
            patch["effort"] = effort
    if body.enabled is not None:
        patch["enabled"] = body.enabled
    if "slack_channel_id" in body.model_fields_set:
        patch["slack_channel_id"] = body.slack_channel_id
    if "slack_notification_mode" in body.model_fields_set:
        patch["slack_notification_mode"] = (
            body.slack_notification_mode or DEFAULT_SLACK_NOTIFICATION_MODE
        )

    updated = {**existing, **patch}
    enabled = bool(updated.get("enabled"))
    retire_cron = (
        not enabled
        or updated.get("schedule") != existing.get("schedule")
        or enabled != bool(existing.get("enabled"))
    )
    if retire_cron:
        # The ritual is idempotent on existence, not on the expression, so a
        # changed schedule has to retire the old cron before asking for one.
        # A create that then fails leaves the schedule not firing until the next
        # update, which beats the double-fire a create-then-delete order risks.
        await _retire_cron(existing)
        updated["cron_id"] = None
    if enabled and not updated.get("cron_id"):
        try:
            updated["cron_id"] = await _register_cron(updated)
        except Exception as exc:
            await put_agent_schedule(updated)
            logger.exception("Failed to recreate schedule cron for %s", schedule_id)
            raise HTTPException(502, "failed to create schedule cron") from exc

    updated = await put_agent_schedule(updated)
    return _schedule_summary(updated, await get_run_state(schedule_id))


async def delete_agent_schedule(schedule_id: str, login: str, *, email: str | None = None) -> None:
    existing = await get_agent_schedule(schedule_id)
    _assert_schedule_owner(existing, login, email)
    assert existing is not None
    await _retire_cron(existing)
    await delete_value(SCHEDULES_NAMESPACE, schedule_id)
    await delete_value(SCHEDULE_RUN_STATE_NAMESPACE, schedule_id)


async def trigger_agent_schedule(
    schedule_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    record = await get_agent_schedule(schedule_id)
    _assert_schedule_owner(record, login, email)
    assert record is not None

    result = await launch_agent_schedule_record(record, test_run=True)
    status = result.get("status")
    if status == "started":
        return result
    if status == "unauthorized":
        status_code = result.get("status_code")
        raise HTTPException(
            status_code if isinstance(status_code, int) else 403,
            result.get("error") or "automation repository unavailable",
        )
    raise HTTPException(502, result.get("error") or "failed to start automation test")
