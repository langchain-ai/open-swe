"""Dashboard-managed recurring agent schedules."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from ..utils.thread_ops import langgraph_client
from .options import SUPPORTED_MODEL_IDS, model_supports_effort
from .profiles import get_profile, get_valid_access_token
from .repo_access import repo_config_for_user, require_repo_access_for_user
from .thread_api import _agent_version_metadata, _now_ms, _resolve_run_email

logger = logging.getLogger(__name__)

SCHEDULES_NAMESPACE: list[str] = ["agent_schedules"]
_AGENT_ASSISTANT_ID = "agent"
_SCHEDULER_ASSISTANT_ID = "scheduler"
_CRON_FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


class ScheduleCreateBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    schedule: str = Field(min_length=1, max_length=120)
    name: str | None = Field(default=None, max_length=120)
    repo: str | None = None
    model_id: str | None = None
    effort: str | None = None

    @field_validator("schedule")
    @classmethod
    def _valid_schedule(cls, value: str) -> str:
        return normalize_cron_schedule(value)


class ScheduleUpdateBody(BaseModel):
    prompt: str | None = Field(default=None, min_length=1, max_length=20_000)
    schedule: str | None = Field(default=None, min_length=1, max_length=120)
    name: str | None = Field(default=None, max_length=120)
    repo: str | None = None
    model_id: str | None = None
    effort: str | None = None
    enabled: bool | None = None

    @field_validator("schedule")
    @classmethod
    def _valid_schedule(cls, value: str | None) -> str | None:
        return normalize_cron_schedule(value) if value is not None else None


def _client():
    return langgraph_client()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_model_choice(
    model_id: str | None, effort: str | None
) -> tuple[str | None, str | None]:
    if not isinstance(model_id, str) or model_id not in SUPPORTED_MODEL_IDS:
        return None, None
    if not isinstance(effort, str) or not model_supports_effort(model_id, effort):
        return None, None
    return model_id, effort


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


def _schedule_summary(record: dict[str, Any]) -> dict[str, Any]:
    repo = record.get("repo") if isinstance(record.get("repo"), dict) else None
    return {
        "id": record.get("id"),
        "name": record.get("name"),
        "prompt": record.get("prompt"),
        "schedule": record.get("schedule"),
        "repo": _repo_full_name(repo),
        "model": record.get("model"),
        "effort": record.get("effort"),
        "enabled": bool(record.get("enabled")),
        "cronId": record.get("cron_id"),
        "lastThreadId": record.get("last_thread_id"),
        "lastRunId": record.get("last_run_id"),
        "lastTriggeredAt": record.get("last_triggered_at"),
        "lastError": record.get("last_error"),
        "lastErrorAt": record.get("last_error_at"),
        "createdAt": record.get("created_at"),
        "updatedAt": record.get("updated_at"),
    }


async def _get_value(schedule_id: str) -> dict[str, Any] | None:
    item = await _client().store.get_item(SCHEDULES_NAMESPACE, schedule_id)
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


async def _put_value(record: dict[str, Any]) -> dict[str, Any]:
    record = {**record, "updated_at": _now_iso()}
    await _client().store.put_item(SCHEDULES_NAMESPACE, record["id"], record)
    return record


async def get_agent_schedule(schedule_id: str) -> dict[str, Any] | None:
    return await _get_value(schedule_id)


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


async def _search_schedule_values(filter: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    limit = 100
    offset = 0
    while True:
        result = await _client().store.search_items(
            SCHEDULES_NAMESPACE,
            filter=filter,
            limit=limit,
            offset=offset,
        )
        items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
        if not items:
            break
        for item in items:
            value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
            if isinstance(value, dict):
                records.append(value)
        if len(items) < limit:
            break
        offset += len(items)
    return records


async def list_agent_schedules(login: str, *, email: str | None = None) -> list[dict[str, Any]]:
    searches: list[dict[str, Any]] = [{"created_by": login}]
    if email and email.strip():
        searches.append({"user_email": email.strip().lower()})

    seen: dict[str, dict[str, Any]] = {}
    for filter in searches:
        for record in await _search_schedule_values(filter):
            schedule_id = record.get("id")
            if isinstance(schedule_id, str) and _user_owns_schedule(record, login, email):
                seen[schedule_id] = record
    records = list(seen.values())
    records.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return [_schedule_summary(record) for record in records]


async def _ensure_dashboard_github_token(login: str) -> None:
    token = await get_valid_access_token(login)
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")


def _build_cron_config(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "configurable": {
            "schedule_id": record["id"],
        },
        "metadata": _agent_version_metadata(),
    }


async def _create_cron(record: dict[str, Any]) -> str:
    cron = await _client().crons.create(
        _SCHEDULER_ASSISTANT_ID,
        schedule=record["schedule"],
        input={"schedule_id": record["id"]},
        config=_build_cron_config(record),
        metadata={
            "kind": "agent_schedule",
            "schedule_id": record["id"],
            "github_login": record.get("created_by"),
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
        await _client().crons.delete(cron_id)
    except Exception:
        logger.debug("Could not delete schedule cron %s", cron_id, exc_info=True)


async def create_agent_schedule(
    login: str, body: ScheduleCreateBody, *, email: str | None = None
) -> dict[str, Any]:
    await _ensure_dashboard_github_token(login)
    profile = await get_profile(login) or {}
    chosen_model, chosen_effort = _normalize_model_choice(body.model_id, body.effort)
    repo = await repo_config_for_user(login, body.repo)
    schedule_id = str(uuid.uuid4())
    now = _now_iso()
    record: dict[str, Any] = {
        "id": schedule_id,
        "name": (body.name or _derive_name(body.prompt)).strip(),
        "prompt": body.prompt.strip(),
        "schedule": body.schedule,
        "repo": repo,
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
        "user_email": (await _resolve_run_email(login, profile) or email or "").strip().lower(),
        "created_at": now,
        "updated_at": now,
    }
    await _put_value(record)
    try:
        cron_id = await _create_cron(record)
    except Exception as exc:
        await _client().store.delete_item(SCHEDULES_NAMESPACE, schedule_id)
        logger.exception("Failed to create schedule cron for %s", schedule_id)
        raise HTTPException(502, "failed to create schedule cron") from exc
    record = await _put_value({**record, "cron_id": cron_id})
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
        model, effort = _normalize_model_choice(body.model_id, body.effort)
        if model and effort:
            patch["model"] = model
            patch["effort"] = effort
    if body.enabled is not None:
        patch["enabled"] = body.enabled

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

    updated = await _put_value(updated)
    return _schedule_summary(updated)


async def delete_agent_schedule(schedule_id: str, login: str, *, email: str | None = None) -> None:
    existing = await get_agent_schedule(schedule_id)
    _assert_schedule_owner(existing, login, email)
    assert existing is not None
    await _delete_cron(existing.get("cron_id"))
    await _client().store.delete_item(SCHEDULES_NAMESPACE, schedule_id)


def _agent_run_metadata(record: dict[str, Any], thread_id: str) -> dict[str, Any]:
    repo = record.get("repo") if isinstance(record.get("repo"), dict) else None
    now_ms = _now_ms()
    metadata: dict[str, Any] = {
        "source": "schedule",
        "schedule_id": record["id"],
        "schedule_name": record.get("name"),
        "github_login": record.get("created_by"),
        "triggering_user_email": record.get("user_email"),
        "title": f"Scheduled: {record.get('name') or 'Agent'}",
        "base_branch": record.get("base_branch") or "main",
        "branch_prefix": record.get("branch_prefix"),
        "model": record.get("model") or "Default",
        "effort": record.get("effort"),
        "created_at_ms": now_ms,
        "updated_at_ms": now_ms,
    }
    if repo and repo.get("owner") and repo.get("name"):
        metadata["repo_owner"] = repo["owner"]
        metadata["repo_name"] = repo["name"]
    return metadata


def _agent_run_config(record: dict[str, Any], thread_id: str) -> dict[str, Any]:
    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "source": "schedule",
        "github_login": record.get("created_by"),
        "user_email": record.get("user_email"),
        "schedule_id": record["id"],
    }
    repo = record.get("repo") if isinstance(record.get("repo"), dict) else None
    if repo and repo.get("owner") and repo.get("name"):
        configurable["repo"] = repo
    model, effort = _normalize_model_choice(record.get("model"), record.get("effort"))
    if model and effort:
        configurable["agent_model_id"] = model
        configurable["agent_effort"] = effort
    return {"configurable": configurable, "metadata": _agent_version_metadata()}


async def launch_scheduled_agent_run(schedule_id: str) -> dict[str, Any]:
    record = await get_agent_schedule(schedule_id)
    if not record:
        return {"status": "missing", "schedule_id": schedule_id}
    if not record.get("enabled"):
        return {"status": "disabled", "schedule_id": schedule_id}

    repo = record.get("repo") if isinstance(record.get("repo"), dict) else None
    full_name = _repo_full_name(repo)
    login = record.get("created_by")
    if full_name:
        if not (isinstance(login, str) and login):
            await _put_value(
                {
                    **record,
                    "last_error": "schedule owner unavailable",
                    "last_error_at": _now_iso(),
                }
            )
            return {
                "status": "unauthorized",
                "schedule_id": schedule_id,
                "error": "schedule owner unavailable",
            }
        try:
            await require_repo_access_for_user(login, full_name)
        except HTTPException as exc:
            await _put_value(
                {
                    **record,
                    "last_error": str(exc.detail),
                    "last_error_at": _now_iso(),
                }
            )
            return {"status": "unauthorized", "schedule_id": schedule_id, "error": exc.detail}

    thread_id = str(uuid.uuid4())
    metadata = _agent_run_metadata(record, thread_id)
    client = _client()
    await client.threads.create(thread_id=thread_id, metadata=metadata, if_exists="do_nothing")
    await client.threads.update(thread_id=thread_id, metadata=metadata)
    run = await client.runs.create(
        thread_id,
        _AGENT_ASSISTANT_ID,
        input={"messages": [{"role": "user", "content": record["prompt"]}]},
        config=_agent_run_config(record, thread_id),
        if_not_exists="create",
        stream_mode=["values", "updates", "messages-tuple"],
        stream_resumable=True,
    )
    run_id = run.get("run_id") if isinstance(run, dict) else getattr(run, "run_id", None)
    now_ms = _now_ms()
    await client.threads.update(
        thread_id=thread_id,
        metadata={"latest_run_id": run_id, "latest_run_status": "pending", "updated_at_ms": now_ms},
    )
    await _put_value(
        {
            **record,
            "last_thread_id": thread_id,
            "last_run_id": run_id,
            "last_triggered_at": _now_iso(),
            "last_error": None,
            "last_error_at": None,
        }
    )
    return {
        "status": "started",
        "schedule_id": schedule_id,
        "thread_id": thread_id,
        "run_id": run_id,
    }
