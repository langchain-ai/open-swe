"""Dashboard-managed recurring agent schedules."""

import logging
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import HTTPException
from langgraph_sdk.errors import ConflictError
from langgraph_sdk.schema import Config
from pydantic import BaseModel, Field, field_validator

from agent.dashboard.admin import is_admin
from agent.dashboard.options import gate_fable_model, normalize_model_choice
from agent.dashboard.profiles import get_profile, get_valid_access_token
from agent.dashboard.repo_access import repo_config_for_user, require_repo_access_for_user
from agent.dashboard.team_settings import get_team_fable_enabled
from agent.dashboard.threads.access import agent_version_metadata, resolve_run_email
from agent.dashboard.user_mappings import slack_id_for_login
from agent.dispatch import create_durable_run
from agent.input_messages import InputMessageContext, build_run_input
from agent.prompts import render_prompt
from agent.slack.client import (
    bind_slack_thread_id,
    delete_slack_thread_associations,
    post_slack_top_level_message_with_ts,
    store_slack_run_mapping,
)
from agent.source_context import SourceContext
from agent.store import delete_value, get_value, now_iso, now_ms, put_value, search_all_values
from agent.utils.thread_ops import langgraph_client
from agent.utils.thread_participants import PARTICIPANT_LOGINS_KEY, merge_participants

logger = logging.getLogger(__name__)

SCHEDULES_NAMESPACE: list[str] = ["agent_schedules"]
SCHEDULE_RUN_STATE_NAMESPACE: list[str] = ["agent_schedule_run_state"]
_AGENT_ASSISTANT_ID = "agent"
_SCHEDULER_ASSISTANT_ID = "scheduler"
_CRON_FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_SLACK_CHANNEL_ID_RE = re.compile(r"^[CG][A-Z0-9]{8,}$")
SlackNotificationMode = Literal["always", "on_action"]
ThreadMode = Literal["new", "reuse"]
_DEFAULT_SLACK_NOTIFICATION_MODE: SlackNotificationMode = "always"
_DEFAULT_THREAD_MODE: ThreadMode = "reuse"
_AUTOMATION_LAUNCH_LOCK_TTL_MINUTES = 5


def _thread_mode(record: dict[str, Any]) -> ThreadMode:
    return "new" if record.get("thread_mode") == "new" else "reuse"


def _normalize_slack_channel_id(value: str | None) -> str | None:
    channel_id = value.strip().upper() if isinstance(value, str) else ""
    if not channel_id:
        return None
    if not _SLACK_CHANNEL_ID_RE.fullmatch(channel_id):
        raise ValueError("slack_channel_id must be a Slack channel ID starting with C or G")
    return channel_id


def _slack_notification_mode(record: dict[str, Any]) -> SlackNotificationMode:
    return "on_action" if record.get("slack_notification_mode") == "on_action" else "always"


class ScheduleCreateBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    schedule: str = Field(min_length=1, max_length=120)
    name: str | None = Field(default=None, max_length=120)
    repo: str | None = None
    model_id: str | None = None
    effort: str | None = None
    slack_channel_id: str | None = None
    slack_notification_mode: SlackNotificationMode = _DEFAULT_SLACK_NOTIFICATION_MODE
    thread_mode: ThreadMode = _DEFAULT_THREAD_MODE
    admin_thread: bool = False

    @field_validator("schedule")
    @classmethod
    def _valid_schedule(cls, value: str) -> str:
        return normalize_cron_schedule(value)

    @field_validator("slack_channel_id")
    @classmethod
    def _valid_slack_channel_id(cls, value: str | None) -> str | None:
        return _normalize_slack_channel_id(value)


class ScheduleUpdateBody(BaseModel):
    prompt: str | None = Field(default=None, min_length=1, max_length=20_000)
    schedule: str | None = Field(default=None, min_length=1, max_length=120)
    name: str | None = Field(default=None, max_length=120)
    repo: str | None = None
    model_id: str | None = None
    effort: str | None = None
    enabled: bool | None = None
    slack_channel_id: str | None = None
    slack_notification_mode: SlackNotificationMode | None = None
    thread_mode: ThreadMode | None = None
    admin_thread: bool | None = None

    @field_validator("schedule")
    @classmethod
    def _valid_schedule(cls, value: str | None) -> str | None:
        return normalize_cron_schedule(value) if value is not None else None

    @field_validator("slack_channel_id")
    @classmethod
    def _valid_slack_channel_id(cls, value: str | None) -> str | None:
        return _normalize_slack_channel_id(value)


def _validate_cron_value(value: str, low: int, high: int) -> None:
    try:
        n = int(value)
    except ValueError as exc:
        raise ValueError("cron fields must use numbers, *, ranges, steps, or lists") from exc
    if n < low or n > high:
        raise ValueError(f"cron value {n} outside allowed range {low}-{high}")


def _validate_cron_field(field: str, low: int, high: int) -> None:
    for segment in field.split(","):
        if not segment:
            raise ValueError("cron fields cannot contain empty list segments")
        base, sep, step = segment.partition("/")
        if sep:
            _validate_cron_value(step, 1, high)
        if base == "*":
            continue
        start, dash, end = base.partition("-")
        if dash:
            _validate_cron_value(start, low, high)
            _validate_cron_value(end, low, high)
            if int(start) > int(end):
                raise ValueError("cron ranges must be ascending")
        else:
            _validate_cron_value(base, low, high)


def normalize_cron_schedule(raw: str) -> str:
    value = " ".join(raw.strip().split())
    parts = value.split(" ")
    if len(parts) != 5:
        raise ValueError("schedule must be a five-field cron expression")
    for part, (low, high) in zip(parts, _CRON_FIELD_RANGES, strict=True):
        _validate_cron_field(part, low, high)
    return value


def _derive_name(prompt: str) -> str:
    return prompt.strip().splitlines()[0][:80] or "Scheduled agent"


def _repo_full_name(repo: dict[str, str] | None) -> str | None:
    if not repo:
        return None
    owner = repo.get("owner")
    name = repo.get("name")
    return f"{owner}/{name}" if owner and name else None


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
        "scope": "workspace",
        "repo": _repo_full_name(repo),
        "slackChannelId": record.get("slack_channel_id"),
        "slackNotificationMode": _slack_notification_mode(record),
        "threadMode": _thread_mode(record),
        "adminThread": record.get("admin_thread") is True,
        "model": record.get("model"),
        "effort": record.get("effort"),
        "enabled": bool(record.get("enabled")),
        "cronId": record.get("cron_id"),
        "lastThreadId": state.get("last_thread_id"),
        "lastRunId": state.get("last_run_id"),
        "lastTriggeredAt": state.get("last_triggered_at"),
        "lastError": state.get("last_error"),
        "lastErrorAt": state.get("last_error_at"),
        "createdBy": record.get("created_by"),
        "updatedBy": record.get("updated_by") or record.get("created_by"),
        "createdAt": record.get("created_at"),
        "updatedAt": record.get("updated_at"),
    }


async def _migrate_workspace_record(
    namespace: list[str], key: str, record: dict[str, Any]
) -> dict[str, Any]:
    if record.get("scope") == "workspace":
        return record
    migrated = {**record, "scope": "workspace"}
    await put_value(namespace, key, migrated)
    return migrated


async def _put_schedule(record: dict[str, Any]) -> dict[str, Any]:
    record = {**record, "updated_at": now_iso()}
    await put_value(SCHEDULES_NAMESPACE, record["id"], record)
    return record


async def _get_run_state(schedule_id: str) -> dict[str, Any] | None:
    return await get_value(SCHEDULE_RUN_STATE_NAMESPACE, schedule_id)


@asynccontextmanager
async def _automation_launch_lock(schedule_id: str) -> AsyncIterator[bool]:
    client = langgraph_client()
    lock_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"open-swe:automation-launch:{schedule_id}"))
    try:
        await client.threads.create(
            thread_id=lock_id,
            if_exists="raise",
            ttl=_AUTOMATION_LAUNCH_LOCK_TTL_MINUTES,
        )
    except ConflictError:
        yield False
        return
    except Exception:
        logger.warning(
            "Failed to acquire automation launch lock for %s", schedule_id, exc_info=True
        )
        yield False
        return
    try:
        yield True
    finally:
        try:
            await client.threads.delete(lock_id)
        except Exception:
            logger.warning(
                "Failed to release automation launch lock for %s", schedule_id, exc_info=True
            )


async def _put_run_state(record: dict[str, Any], patch: dict[str, Any]) -> None:
    schedule_id = record["id"]
    existing = await _get_run_state(schedule_id)
    fallback = {
        "last_thread_id": record.get("last_thread_id"),
        "last_run_id": record.get("last_run_id"),
        "last_triggered_at": record.get("last_triggered_at"),
        "last_error": record.get("last_error"),
        "last_error_at": record.get("last_error_at"),
    }
    value = {
        **fallback,
        **(existing or {}),
        **patch,
        "schedule_id": schedule_id,
        "scope": "workspace",
        "created_by": record.get("created_by"),
        "user_email": record.get("user_email"),
    }
    await put_value(SCHEDULE_RUN_STATE_NAMESPACE, schedule_id, value)


async def get_agent_schedule(schedule_id: str) -> dict[str, Any] | None:
    record = await get_value(SCHEDULES_NAMESPACE, schedule_id)
    if not record:
        return None
    return await _migrate_workspace_record(SCHEDULES_NAMESPACE, schedule_id, record)


def _assert_schedule_exists(record: dict[str, Any] | None) -> None:
    if not record:
        raise HTTPException(404, "schedule not found")


async def list_agent_schedules() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for record in await search_all_values(SCHEDULES_NAMESPACE):
        schedule_id = record.get("id")
        if isinstance(schedule_id, str):
            records.append(
                await _migrate_workspace_record(SCHEDULES_NAMESPACE, schedule_id, record)
            )
    run_states: dict[str, dict[str, Any]] = {}
    for state in await search_all_values(SCHEDULE_RUN_STATE_NAMESPACE):
        schedule_id = state.get("schedule_id")
        if isinstance(schedule_id, str):
            run_states[schedule_id] = await _migrate_workspace_record(
                SCHEDULE_RUN_STATE_NAMESPACE, schedule_id, state
            )
    records.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return [_schedule_summary(record, run_states.get(record["id"])) for record in records]


async def _ensure_dashboard_github_token(login: str) -> None:
    token = await get_valid_access_token(login)
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")


def _build_cron_config(record: dict[str, Any]) -> Config:
    return {
        "configurable": {
            "schedule_id": record["id"],
        },
    }


async def _create_cron(record: dict[str, Any]) -> str:
    cron = await langgraph_client().crons.create(
        _SCHEDULER_ASSISTANT_ID,
        schedule=record["schedule"],
        input={"schedule_id": record["id"]},
        config=_build_cron_config(record),
        metadata={
            "kind": "agent_schedule",
            "schedule_id": record["id"],
            "github_login": record.get("created_by"),
            **agent_version_metadata(),
        },
    )
    cron_id = cron.get("cron_id") if isinstance(cron, dict) else getattr(cron, "cron_id", None)
    if not isinstance(cron_id, str) or not cron_id:
        raise RuntimeError("cron creation did not return a cron_id")
    return cron_id


async def _delete_cron(cron_id: str | None) -> None:
    if not cron_id:
        return
    try:
        await langgraph_client().crons.delete(cron_id)
    except Exception:
        logger.debug("Could not delete schedule cron %s", cron_id, exc_info=True)


async def create_agent_schedule(
    login: str,
    body: ScheduleCreateBody,
    *,
    email: str | None = None,
    allow_admin_thread: bool = False,
) -> dict[str, Any]:
    if body.admin_thread and not allow_admin_thread:
        raise HTTPException(403, "admin only")
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
        "thread_mode": body.thread_mode,
        "admin_thread": body.admin_thread,
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
        "scope": "workspace",
        "created_by": login,
        "updated_by": login,
        "user_email": (await resolve_run_email(login, profile) or email or "").strip().lower(),
        "created_at": now,
        "updated_at": now,
    }
    await _put_schedule(record)
    try:
        cron_id = await _create_cron(record)
    except Exception as exc:
        await delete_value(SCHEDULES_NAMESPACE, schedule_id)
        logger.exception("Failed to create schedule cron for %s", schedule_id)
        raise HTTPException(502, "failed to create schedule cron") from exc
    record = await _put_schedule({**record, "cron_id": cron_id})
    return _schedule_summary(record)


async def _detach_reusable_slack_thread(schedule_id: str) -> None:
    thread_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"open-swe:automation:{schedule_id}"))
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception:  # noqa: BLE001
        logger.debug("Could not load reusable automation thread %s", thread_id)
        return
    metadata = thread.get("metadata")
    context = SourceContext.from_metadata(metadata)
    if context.slack_thread is None:
        return
    await delete_slack_thread_associations(
        client,
        context.slack_thread.channel_id,
        context.slack_thread.thread_ts,
        expected_thread_id=thread_id,
    )
    await client.threads.update(thread_id=thread_id, metadata={"source_context": None})


async def update_agent_schedule(
    schedule_id: str,
    login: str,
    body: ScheduleUpdateBody,
    *,
    email: str | None = None,
    allow_admin_thread: bool = False,
) -> dict[str, Any]:
    existing = await get_agent_schedule(schedule_id)
    _assert_schedule_exists(existing)
    assert existing is not None
    if (
        body.admin_thread is True
        and existing.get("admin_thread") is not True
        and not allow_admin_thread
    ):
        raise HTTPException(403, "admin only")

    patch: dict[str, Any] = {"scope": "workspace", "updated_by": login}
    if body.prompt is not None:
        patch["prompt"] = body.prompt.strip()
    if body.schedule is not None:
        patch["schedule"] = body.schedule
    if body.name is not None:
        patch["name"] = body.name.strip() or _derive_name(patch.get("prompt", existing["prompt"]))
    if body.repo is not None:
        patch["repo"] = await repo_config_for_user(existing["created_by"], body.repo)
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
            body.slack_notification_mode or _DEFAULT_SLACK_NOTIFICATION_MODE
        )
    if "thread_mode" in body.model_fields_set:
        patch["thread_mode"] = body.thread_mode or _DEFAULT_THREAD_MODE

    detach_reusable_slack = _thread_mode(existing) == "reuse" and (
        patch.get("thread_mode", "reuse") != "reuse"
        or patch.get("enabled") is False
        or (
            "slack_channel_id" in patch
            and patch["slack_channel_id"] != existing.get("slack_channel_id")
        )
        or (
            "slack_notification_mode" in patch
            and patch["slack_notification_mode"] != existing.get("slack_notification_mode")
        )
    )
    if body.admin_thread is not None:
        patch["admin_thread"] = body.admin_thread

    updated = {**existing, **patch}
    schedule_changed = updated.get("schedule") != existing.get("schedule")
    enabled_changed = updated.get("enabled") != existing.get("enabled")
    needs_new_cron = bool(updated.get("enabled")) and (schedule_changed or enabled_changed)

    if needs_new_cron:
        try:
            new_cron_id = await _create_cron(updated)
        except Exception as exc:
            logger.exception("Failed to recreate schedule cron for %s", schedule_id)
            raise HTTPException(502, "failed to create schedule cron") from exc
        await _delete_cron(existing.get("cron_id"))
        updated["cron_id"] = new_cron_id
    elif updated.get("enabled") is False and existing.get("cron_id"):
        await _delete_cron(existing.get("cron_id"))
        updated["cron_id"] = None

    updated = await _put_schedule(updated)
    if detach_reusable_slack:
        await _detach_reusable_slack_thread(schedule_id)
    return _schedule_summary(updated, await _get_run_state(schedule_id))


async def delete_agent_schedule(schedule_id: str) -> None:
    existing = await get_agent_schedule(schedule_id)
    _assert_schedule_exists(existing)
    assert existing is not None
    if _thread_mode(existing) == "reuse":
        await _detach_reusable_slack_thread(schedule_id)
    await _delete_cron(existing.get("cron_id"))
    await delete_value(SCHEDULES_NAMESPACE, schedule_id)
    await delete_value(SCHEDULE_RUN_STATE_NAMESPACE, schedule_id)


def _slack_root_message(record: dict[str, Any], *, test_run: bool = False) -> str:
    repo = _repo_full_name(record.get("repo") if isinstance(record.get("repo"), dict) else None)
    repo_line = f"\n*Repository:* `{repo}`" if repo else ""
    run_kind = "test" if test_run else "scheduled"
    return (
        f"*Open SWE automation:* {record.get('name') or 'Scheduled agent'}{repo_line}\n\n"
        f"A {run_kind} run started. Reply in this thread to follow up with the agent."
    )


def _scheduled_prompt(record: dict[str, Any], slack_thread: dict[str, Any] | None) -> str:
    prompt = str(record["prompt"])
    if slack_thread:
        return render_prompt("runs/scheduled-slack-thread.md", prompt=prompt)
    slack_channel_id = record.get("slack_channel_id")
    if (
        _slack_notification_mode(record) == "on_action"
        and isinstance(slack_channel_id, str)
        and slack_channel_id
    ):
        return render_prompt("runs/scheduled-notify-on-action.md", prompt=prompt)
    return prompt


def _admin_thread_enabled(record: dict[str, Any]) -> bool:
    email = record.get("user_email")
    login = record.get("created_by")
    return record.get("admin_thread") is True and is_admin(
        email if isinstance(email, str) else None,
        login=login if isinstance(login, str) else None,
    )


def _agent_run_metadata(
    record: dict[str, Any],
    thread_id: str,
    slack_thread: dict[str, Any] | None = None,
    *,
    test_run: bool = False,
    admin_thread: bool = False,
) -> dict[str, Any]:
    repo = record.get("repo") if isinstance(record.get("repo"), dict) else None
    created_ms = now_ms()
    title_prefix = "Test" if test_run else "Scheduled"
    metadata: dict[str, Any] = {
        "source": "schedule",
        "origin": "schedule",
        "thread_category": "automation",
        "trigger_kind": "schedule_test" if test_run else "schedule",
        "schedule_id": record["id"],
        "automation_scope": "workspace",
        "schedule_name": record.get("name"),
        "schedule_test": test_run,
        "github_login": record.get("created_by"),
        PARTICIPANT_LOGINS_KEY: merge_participants(None, record.get("created_by")),
        "triggering_user_email": record.get("user_email"),
        "title": f"{title_prefix}: {record.get('name') or 'Agent'}",
        "base_branch": record.get("base_branch") or "main",
        "branch_prefix": record.get("branch_prefix"),
        "model": record.get("model") or "Default",
        "effort": record.get("effort"),
        "created_at_ms": created_ms,
        "updated_at_ms": created_ms,
    }
    if repo and repo.get("owner") and repo.get("name"):
        metadata["repo_owner"] = repo["owner"]
        metadata["repo_name"] = repo["name"]
    if slack_thread:
        metadata["source_context"] = SourceContext.parse({"slack_thread": slack_thread}).dump()
    elif _thread_mode(record) == "reuse":
        metadata["source_context"] = None
    if admin_thread:
        metadata["admin_thread"] = True
    return metadata


async def _agent_run_config(
    record: dict[str, Any],
    thread_id: str,
    slack_thread: dict[str, Any] | None = None,
    *,
    test_run: bool = False,
    admin_thread: bool = False,
) -> dict[str, Any]:
    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "source": "schedule",
        "github_login": record.get("created_by"),
        "user_email": record.get("user_email"),
        "schedule_id": record["id"],
        "schedule_test": test_run,
        "prepare_run_id": str(uuid.uuid4()),
    }
    repo = record.get("repo") if isinstance(record.get("repo"), dict) else None
    if repo and repo.get("owner") and repo.get("name"):
        configurable["repo"] = repo
    if slack_thread:
        configurable["slack_thread"] = slack_thread
    if admin_thread:
        configurable["admin_thread"] = True
    slack_channel_id = record.get("slack_channel_id")
    if (
        _slack_notification_mode(record) == "on_action"
        and isinstance(slack_channel_id, str)
        and slack_channel_id
    ):
        configurable["automation_slack_notification"] = {
            "channel_id": slack_channel_id,
            "mode": "on_action",
            "schedule_id": record["id"],
            "schedule_name": record.get("name"),
        }
    model, effort = normalize_model_choice(record.get("model"), record.get("effort"))
    if model and effort:
        model, effort = gate_fable_model(
            model, effort, fable_enabled=await get_team_fable_enabled()
        )
        configurable["agent_model_id"] = model
        configurable["agent_effort"] = effort
    return {"configurable": configurable, "metadata": agent_version_metadata()}


async def _launch_agent_schedule_record(
    record: dict[str, Any], *, test_run: bool = False
) -> dict[str, Any]:
    if _thread_mode(record) == "reuse":
        async with _automation_launch_lock(record["id"]) as acquired:
            if not acquired:
                return {
                    "status": "busy",
                    "schedule_id": record["id"],
                    "error": "automation launch already in progress",
                }
            return await _launch_agent_schedule_record_unlocked(record, test_run=test_run)
    return await _launch_agent_schedule_record_unlocked(record, test_run=test_run)


async def _launch_agent_schedule_record_unlocked(
    record: dict[str, Any], *, test_run: bool = False
) -> dict[str, Any]:
    schedule_id = record["id"]
    if not test_run and not record.get("enabled"):
        return {"status": "disabled", "schedule_id": schedule_id}

    repo = record.get("repo") if isinstance(record.get("repo"), dict) else None
    full_name = _repo_full_name(repo)
    login = record.get("created_by")
    if full_name:
        if not (isinstance(login, str) and login):
            await _put_run_state(
                record,
                {
                    "last_error": "schedule owner unavailable",
                    "last_error_at": now_iso(),
                },
            )
            return {
                "status": "unauthorized",
                "schedule_id": schedule_id,
                "error": "schedule owner unavailable",
            }
        try:
            await require_repo_access_for_user(login, full_name)
        except HTTPException as exc:
            await _put_run_state(
                record,
                {
                    "last_error": str(exc.detail),
                    "last_error_at": now_iso(),
                },
            )
            return {
                "status": "unauthorized",
                "schedule_id": schedule_id,
                "error": exc.detail,
                "status_code": exc.status_code,
            }

    client = langgraph_client()
    reuse_thread = _thread_mode(record) == "reuse"
    thread_id = (
        str(uuid.uuid5(uuid.NAMESPACE_URL, f"open-swe:automation:{schedule_id}"))
        if reuse_thread
        else str(uuid.uuid4())
    )
    existing_metadata: dict[str, Any] = {}
    if reuse_thread:
        try:
            existing_thread = await client.threads.get(thread_id)
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "status_code", None) != 404:
                error = "failed to load reusable automation thread"
                logger.exception("Failed to load automation thread %s", thread_id)
                await _put_run_state(
                    record,
                    {"last_error": error, "last_error_at": now_iso()},
                )
                return {"status": "error", "schedule_id": schedule_id, "error": error}
        else:
            raw_metadata = existing_thread.get("metadata")
            if isinstance(raw_metadata, dict):
                existing_metadata = raw_metadata
    slack_thread: dict[str, Any] | None = None
    existing_context = SourceContext.from_metadata(existing_metadata)
    existing_slack_thread = (
        existing_context.dump()["slack_thread"]
        if existing_context.slack_thread is not None
        else None
    )
    slack_channel_id = record.get("slack_channel_id")
    if (
        _slack_notification_mode(record) == "always"
        and isinstance(slack_channel_id, str)
        and slack_channel_id
    ):
        if (
            isinstance(existing_slack_thread, dict)
            and existing_slack_thread.get("channel_id") == slack_channel_id
            and isinstance(existing_slack_thread.get("thread_ts"), str)
        ):
            slack_thread = existing_slack_thread
        else:
            message_ts, slack_error = await post_slack_top_level_message_with_ts(
                slack_channel_id,
                _slack_root_message(record, test_run=test_run),
                unfurl_links=False,
                unfurl_media=False,
            )
            if not message_ts:
                error = f"Slack post failed: {slack_error or 'unknown error'}"
                await _put_run_state(
                    record,
                    {"last_error": error, "last_error_at": now_iso()},
                )
                return {"status": "error", "schedule_id": schedule_id, "error": error}
            slack_thread = {
                "channel_id": slack_channel_id,
                "thread_ts": message_ts,
                "triggering_event_ts": message_ts,
                "triggering_user_id": await slack_id_for_login(
                    record.get("created_by") if isinstance(record.get("created_by"), str) else None
                )
                or "",
                "triggering_user_email": record.get("user_email") or "",
            }
            await bind_slack_thread_id(client, slack_channel_id, message_ts, thread_id)

    if (
        reuse_thread
        and isinstance(existing_slack_thread, dict)
        and existing_slack_thread is not slack_thread
        and isinstance(existing_slack_thread.get("channel_id"), str)
        and isinstance(existing_slack_thread.get("thread_ts"), str)
    ):
        await delete_slack_thread_associations(
            client,
            existing_slack_thread["channel_id"],
            existing_slack_thread["thread_ts"],
            expected_thread_id=thread_id,
        )

    admin_thread = _admin_thread_enabled(record)
    metadata = _agent_run_metadata(
        record,
        thread_id,
        slack_thread,
        test_run=test_run,
        admin_thread=admin_thread,
    )
    if reuse_thread and isinstance(existing_metadata.get("created_at_ms"), (int, float)):
        metadata["created_at_ms"] = existing_metadata["created_at_ms"]
    await client.threads.create(thread_id=thread_id, metadata=metadata, if_exists="do_nothing")
    await client.threads.update(thread_id=thread_id, metadata=metadata)
    input_context: InputMessageContext = {
        "sender_id": f"system:schedule:{schedule_id}",
        "surface": "automation",
        "kind": "system",
    }
    if isinstance(slack_channel_id, str) and slack_channel_id:
        input_context["channel_id"] = f"slack:{slack_channel_id}"
    run = await create_durable_run(
        thread_id,
        _AGENT_ASSISTANT_ID,
        input=build_run_input(
            _scheduled_prompt(record, slack_thread),
            input_context,
            systems=[
                {
                    "id": f"system:schedule:{schedule_id}",
                    "display_name": record.get("name") or "Scheduled automation",
                    "platform": "open-swe",
                }
            ],
            channels=(
                [{"id": f"slack:{slack_channel_id}", "platform": "slack"}]
                if isinstance(slack_channel_id, str) and slack_channel_id
                else None
            ),
        ),
        source="schedule",
        config=await _agent_run_config(
            record,
            thread_id,
            slack_thread,
            test_run=test_run,
            admin_thread=admin_thread,
        ),
        client=client,
        multitask_strategy="enqueue" if reuse_thread else "interrupt",
        stream_resumable=True,
    )
    run_id = run.get("run_id") if isinstance(run, dict) else getattr(run, "run_id", None)
    if slack_thread and isinstance(run_id, str) and run_id:
        await store_slack_run_mapping(
            client,
            slack_thread["channel_id"],
            slack_thread["thread_ts"],
            run_id,
            message_ts=slack_thread["thread_ts"],
        )
    await client.threads.update(
        thread_id=thread_id,
        metadata={
            "latest_run_id": run_id,
            "latest_run_status": "pending",
            "updated_at_ms": now_ms(),
        },
    )
    await _put_run_state(
        record,
        {
            "last_thread_id": thread_id,
            "last_run_id": run_id,
            "last_triggered_at": now_iso(),
            "last_error": None,
            "last_error_at": None,
        },
    )
    return {
        "status": "started",
        "schedule_id": schedule_id,
        "thread_id": thread_id,
        "run_id": run_id,
    }


async def launch_scheduled_agent_run(schedule_id: str) -> dict[str, Any]:
    record = await get_agent_schedule(schedule_id)
    if not record:
        return {"status": "missing", "schedule_id": schedule_id}
    return await _launch_agent_schedule_record(record)


async def trigger_agent_schedule(schedule_id: str) -> dict[str, Any]:
    record = await get_agent_schedule(schedule_id)
    _assert_schedule_exists(record)
    assert record is not None

    result = await _launch_agent_schedule_record(record, test_run=True)
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
