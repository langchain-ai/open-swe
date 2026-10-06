"""Automations: a stored prompt that runs in one workspace whenever one of its triggers fires.

Records live in PostgreSQL (``automation`` and ``automation_trigger``); each
schedule trigger owns one LangGraph cron, whose input names the automation.
GitHub triggers fire from the GitHub webhook, and every inbound event is
claimed once per automation in ``event_claim`` before it runs.
"""

import json
import logging
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import HTTPException
from langgraph_sdk.schema import Config
from pydantic import BaseModel, Field, TypeAdapter, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent import event_claims
from agent.dashboard.admin import is_admin
from agent.dashboard.options import gate_fable_model, normalize_model_choice
from agent.dashboard.profiles import get_profile, get_valid_access_token
from agent.dashboard.repo_access import (
    repo_config_for_user,
    repo_config_for_workspace,
    repo_is_private,
    require_repo_access_for_workspace,
)
from agent.dashboard.workspace_settings import get_workspace_settings
from agent.database import postgres
from agent.database.postgres import transaction
from agent.dispatch import create_durable_run
from agent.github.comments import fence_github_comment_body
from agent.github.org_membership import OPEN_SWE_GITHUB_LOGINS
from agent.github.token_scope import GITHUB_TOKEN_REPOSITORIES_KEY, event_token_repositories
from agent.input_messages import InputMessageContext, build_run_input
from agent.invocation import new_invocation_id, with_invocation_id
from agent.prompts import prompt
from agent.review.styles import normalize_repo_full_name
from agent.run_config import RunConfig
from agent.slack.channels import SlackChannel
from agent.slack.payloads import SlackChannelContext, SlackEvent, SlackEventEnvelope
from agent.store import delete_value, get_value, now_iso, now_ms, search_all_values
from agent.threads.access import agent_version_metadata, resolve_run_email
from agent.threads.creation import create_thread
from agent.users import User
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_client
from agent.webhooks.common import enforce_public_repo_org_gate, repo_private_from_payload
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG, WORKSPACES, slugify

logger = logging.getLogger(__name__)

# The LangGraph Store namespaces automations lived in before PostgreSQL; read
# only by the one-time import.
SCHEDULES_NAMESPACE: list[str] = ["agent_schedules"]
SCHEDULE_RUN_STATE_NAMESPACE: list[str] = ["agent_schedule_run_state"]
_AGENT_ASSISTANT_ID = "agent"
_SCHEDULER_ASSISTANT_ID = "scheduler"
_CRON_FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
GitHubEvent = Literal[
    "issues.opened", "pull_request.opened", "pull_request.closed", "pull_request.merged"
]
# How a run's prompt names each GitHub event; "closed" also fires for merges.
GITHUB_EVENT_DESCRIPTIONS: dict[GitHubEvent, str] = {
    "issues.opened": "an issue was opened",
    "pull_request.opened": "a pull request was opened",
    "pull_request.closed": "a pull request was closed",
    "pull_request.merged": "a pull request was merged",
}
SlackTriggerEvent = Literal["message.posted"]
SLACK_EVENT_DESCRIPTIONS: dict[SlackTriggerEvent, str] = {
    "message.posted": "a message was posted",
}
SlackSenders = Literal["anyone", "people", "bots"]
_SLACK_TRIGGER_CHANNEL_RE = re.compile(r"^[CG][A-Z0-9]{8,}$")
_SLACK_MESSAGE_MAX_CHARS = 8_000
LinearTriggerEvent = Literal["issue.created", "issue.labeled"]
LINEAR_EVENT_DESCRIPTIONS: dict[LinearTriggerEvent, str] = {
    "issue.created": "an issue was created",
    "issue.labeled": "a label was added to an issue",
}
_LINEAR_TEAM_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,9}$")
_LINEAR_DESCRIPTION_MAX_CHARS = 8_000
_RATE_WINDOW = timedelta(hours=1)
_DELIVERY_CLAIM_SCOPE = "automation_delivery"
_DELIVERY_CLAIM_TTL = timedelta(hours=24)
_NEW_CRON_GRACE = timedelta(minutes=5)


def _normalized_repo(value: str) -> str:
    return normalize_repo_full_name(value)


class ScheduleTrigger(BaseModel):
    kind: Literal["schedule"] = "schedule"
    cron: str = Field(min_length=1, max_length=120)

    @field_validator("cron")
    @classmethod
    def _valid_cron(cls, value: str) -> str:
        return normalize_cron_schedule(value)


class GitHubTrigger(BaseModel):
    """Fires on events in one repository; its runs work in that repository."""

    kind: Literal["github"] = "github"
    repo: str = Field(min_length=3)
    events: list[GitHubEvent] = Field(min_length=1)

    @field_validator("repo")
    @classmethod
    def _valid_repo(cls, value: str) -> str:
        return _normalized_repo(value)


class SlackTrigger(BaseModel):
    """Fires on messages in one Slack channel Open SWE is a member of."""

    kind: Literal["slack"] = "slack"
    channel: str = Field(min_length=9, max_length=40)
    events: list[SlackTriggerEvent] = Field(min_length=1)
    senders: SlackSenders = "anyone"
    # Case-insensitive regular expression the message text must match.
    match: str | None = Field(default=None, max_length=200)
    max_runs_per_hour: int | None = Field(default=None, ge=1, le=100)

    @field_validator("channel")
    @classmethod
    def _valid_channel(cls, value: str) -> str:
        channel = value.strip().upper()
        if not _SLACK_TRIGGER_CHANNEL_RE.fullmatch(channel):
            raise ValueError("channel must be a Slack channel ID starting with C or G")
        return channel

    @field_validator("match")
    @classmethod
    def _valid_match(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"match is not a valid regular expression: {exc}") from exc
        return value


class LinearTrigger(BaseModel):
    """Fires on issue events in one Linear team."""

    kind: Literal["linear"] = "linear"
    team: str = Field(min_length=1, max_length=10)
    events: list[LinearTriggerEvent] = Field(min_length=1)
    # Issue labels, by name: an issue must carry one, or for issue.labeled, gain one.
    labels: list[str] = Field(default_factory=list, max_length=20)
    project: str | None = Field(default=None, max_length=200)
    max_runs_per_hour: int | None = Field(default=None, ge=1, le=100)

    @field_validator("team")
    @classmethod
    def _valid_team(cls, value: str) -> str:
        team = value.strip().upper()
        if not _LINEAR_TEAM_KEY_RE.fullmatch(team):
            raise ValueError("team must be a Linear team key, such as ENG")
        return team

    @field_validator("labels")
    @classmethod
    def _valid_labels(cls, value: list[str]) -> list[str]:
        return [label.strip() for label in value if label.strip()]

    @field_validator("project")
    @classmethod
    def _valid_project(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None


TriggerConfig = Annotated[
    ScheduleTrigger | GitHubTrigger | SlackTrigger | LinearTrigger, Field(discriminator="kind")
]
_TRIGGERS = TypeAdapter(list[TriggerConfig])


class ScheduleCreateBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    # Every trigger, any of which fires the automation, each with its own filters.
    triggers: list[TriggerConfig] = Field(min_length=1)
    name: str | None = Field(default=None, max_length=120)
    model_id: str | None = None
    effort: str | None = None
    admin_thread: bool = False
    # The workspace its runs launch in, always chosen explicitly.
    workspace: str = Field(min_length=1, max_length=120)


class ScheduleUpdateBody(BaseModel):
    prompt: str | None = Field(default=None, min_length=1, max_length=20_000)
    # Replaces every trigger.
    triggers: list[TriggerConfig] | None = Field(default=None, min_length=1)
    name: str | None = Field(default=None, max_length=120)
    model_id: str | None = None
    effort: str | None = None
    enabled: bool | None = None
    admin_thread: bool | None = None
    workspace: str | None = None


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
    triggers = record.get("triggers") or []
    schedule = next(
        ((t.get("config") or {}).get("cron") for t in triggers if t.get("kind") == "schedule"),
        None,
    )
    cron_ids = [t["cron_id"] for t in triggers if t.get("cron_id")]
    return {
        "id": record.get("id"),
        "name": record.get("name"),
        "prompt": record.get("prompt"),
        "schedule": schedule,
        "triggers": [{"id": t["id"], **(t.get("config") or {})} for t in triggers],
        "scope": "workspace",
        "workspace": _record_workspace(record),
        "adminThread": record.get("admin_thread") is True,
        "model": record.get("model"),
        "effort": record.get("effort"),
        "enabled": bool(record.get("enabled")),
        "cronId": cron_ids[0] if cron_ids else None,
        "lastThreadId": record.get("last_thread_id"),
        "lastRunId": record.get("last_run_id"),
        "lastTriggeredAt": record.get("last_triggered_at"),
        "lastError": record.get("last_error"),
        "lastErrorAt": record.get("last_error_at"),
        "createdBy": record.get("created_by"),
        "updatedBy": record.get("updated_by") or record.get("created_by"),
        "createdAt": record.get("created_at"),
        "updatedAt": record.get("updated_at"),
    }


def _record_workspace(record: dict[str, Any]) -> str:
    workspace = record.get("workspace")
    return workspace if isinstance(workspace, str) and workspace else DEFAULT_WORKSPACE_SLUG


async def _existing_workspace(value: str) -> str:
    """``value`` as a workspace slug, refusing one that does not exist."""
    try:
        slug = slugify(value)
    except ValueError as exc:
        raise HTTPException(422, "workspace must be a workspace name or slug") from exc
    if not await WORKSPACES.slug_exists(slug):
        raise HTTPException(422, f"no workspace named {slug!r}")
    return slug


def _iso(value: object) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError, AttributeError, TypeError:
        return None


_SELECT_AUTOMATIONS = """
    SELECT a.*, w.slug AS workspace_slug
    FROM automation a JOIN workspace w ON w.id = a.workspace_id
"""


def _match_key(trigger: TriggerConfig) -> str | None:
    if isinstance(trigger, GitHubTrigger):
        return trigger.repo.lower()
    if isinstance(trigger, SlackTrigger):
        return trigger.channel
    if isinstance(trigger, LinearTrigger):
        return trigger.team
    return None


async def _load_records(
    conn: AsyncConnection, where: str = "", params: dict[str, object] | None = None
) -> list[dict[str, Any]]:
    rows = (
        (await conn.execute(text(f"{_SELECT_AUTOMATIONS} {where}"), params or {})).mappings().all()
    )
    if not rows:
        return []
    trigger_rows = (
        (
            await conn.execute(
                text(
                    "SELECT * FROM automation_trigger WHERE automation_id = ANY(:ids) "
                    "ORDER BY created_at, id"
                ),
                {"ids": [row["id"] for row in rows]},
            )
        )
        .mappings()
        .all()
    )
    triggers_by_automation: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for row in trigger_rows:
        triggers_by_automation.setdefault(row["automation_id"], []).append(
            {
                "id": str(row["id"]),
                "kind": row["kind"],
                "config": dict(row["config"]),
                "cron_id": row["cron_id"],
            }
        )
    records = []
    for row in rows:
        records.append(
            {
                "id": str(row["id"]),
                "workspace_id": row["workspace_id"],
                "workspace": row["workspace_slug"],
                "name": row["name"],
                "prompt": row["prompt"],
                "admin_thread": row["admin_thread"],
                "model": row["model"],
                "effort": row["effort"],
                "base_branch": row["base_branch"],
                "branch_prefix": row["branch_prefix"],
                "enabled": row["enabled"],
                "created_by": row["created_by"],
                "updated_by": row["updated_by"],
                "user_email": row["user_email"],
                "created_at": _iso(row["created_at"]),
                "updated_at": _iso(row["updated_at"]),
                "last_thread_id": row["last_thread_id"],
                "last_run_id": row["last_run_id"],
                "last_triggered_at": _iso(row["last_triggered_at"]),
                "last_error": row["last_error"],
                "last_error_at": _iso(row["last_error_at"]),
                "triggers": triggers_by_automation.get(row["id"], []),
            }
        )
    return records


async def _insert_triggers(
    conn: AsyncConnection,
    automation_id: str,
    triggers: Sequence[TriggerConfig],
    trigger_ids: Sequence[uuid.UUID],
    cron_ids: Sequence[str | None],
) -> None:
    for trigger, trigger_id, cron_id in zip(triggers, trigger_ids, cron_ids, strict=True):
        await conn.execute(
            text(
                "INSERT INTO automation_trigger (id, automation_id, kind, config, match_key, "
                "cron_id) VALUES (:id, :automation_id, :kind, CAST(:config AS jsonb), "
                ":match_key, :cron_id)"
            ),
            {
                "id": trigger_id,
                "automation_id": uuid.UUID(automation_id),
                "kind": trigger.kind,
                "config": json.dumps(trigger.model_dump(mode="json")),
                "match_key": _match_key(trigger),
                "cron_id": cron_id,
            },
        )


_RUN_STATE_FIELDS = (
    "last_thread_id",
    "last_run_id",
    "last_triggered_at",
    "last_error",
    "last_error_at",
)


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value)
    return None


async def _put_run_state(record: dict[str, Any], patch: dict[str, Any]) -> None:
    """Write only run-state columns, so a launch never overwrites a concurrent edit."""
    values = {key: value for key, value in patch.items() if key in _RUN_STATE_FIELDS}
    if not values:
        return
    for key in ("last_triggered_at", "last_error_at"):
        if key in values:
            values[key] = _timestamp(values[key])
    assignments = ", ".join(f"{key} = :{key}" for key in values)
    async with transaction() as conn:
        await conn.execute(
            text(f"UPDATE automation SET {assignments} WHERE id = :id"),
            {**values, "id": uuid.UUID(record["id"])},
        )


async def get_agent_schedule(schedule_id: str) -> dict[str, Any] | None:
    automation_id = _uuid(schedule_id)
    if automation_id is None:
        return None
    async with transaction() as conn:
        records = await _load_records(conn, "WHERE a.id = :id", {"id": automation_id})
    return records[0] if records else None


def _assert_schedule_exists(record: dict[str, Any] | None) -> None:
    if not record:
        raise HTTPException(404, "schedule not found")


async def list_agent_schedules() -> list[dict[str, Any]]:
    async with transaction() as conn:
        records = await _load_records(conn, "ORDER BY a.updated_at DESC")
    return [_schedule_summary(record) for record in records]


async def _ensure_dashboard_github_token(login: str) -> None:
    token = await get_valid_access_token(login)
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")


def _build_cron_config(automation_id: str) -> Config:
    return {"configurable": {"schedule_id": automation_id}}


async def _create_cron(
    automation_id: str, trigger_id: uuid.UUID, cron: str, created_by: str | None
) -> str:
    created = await langgraph_client().crons.create(
        _SCHEDULER_ASSISTANT_ID,
        schedule=cron,
        input={"schedule_id": automation_id, "trigger_id": str(trigger_id)},
        config=_build_cron_config(automation_id),
        metadata={
            "kind": "agent_schedule",
            "schedule_id": automation_id,
            "created_by": created_by,
            "owner_type": "system",
            "visibility": "public",
            **agent_version_metadata(),
        },
    )
    cron_id = (
        created.get("cron_id") if isinstance(created, dict) else getattr(created, "cron_id", None)
    )
    if not isinstance(cron_id, str) or not cron_id:
        raise RuntimeError("cron creation did not return a cron_id")
    return cron_id


async def _delete_cron(cron_id: str | None) -> bool:
    if not cron_id:
        return True
    try:
        await langgraph_client().crons.delete(cron_id)
    except Exception:
        logger.warning("Could not delete schedule cron", extra={"cron_id": cron_id}, exc_info=True)
        return False
    return True


async def _delete_orphan_crons(automation_id: str, keep: set[str]) -> None:
    """Delete crons for ``automation_id`` that no trigger owns any more.

    A cron whose delete failed when its trigger was removed keeps firing; the
    next time it does, it finds itself here and goes.
    """
    try:
        crons = await langgraph_client().crons.search(
            assistant_id=_SCHEDULER_ASSISTANT_ID,
            metadata={"schedule_id": automation_id},
            limit=100,
        )
    except Exception:
        logger.warning(
            "Could not look up orphaned automation crons",
            extra={"schedule_id": automation_id},
            exc_info=True,
        )
        return
    settled_before = datetime.now(UTC) - _NEW_CRON_GRACE
    for cron in crons:
        cron_id = cron.get("cron_id")
        created_at = _timestamp(cron.get("created_at"))
        # An edit makes its crons before it saves the triggers that own them.
        if created_at is not None and created_at > settled_before:
            continue
        if isinstance(cron_id, str) and cron_id not in keep:
            await _delete_cron(cron_id)


async def _create_crons(
    automation_id: str,
    triggers: Sequence[TriggerConfig],
    trigger_ids: Sequence[uuid.UUID],
    *,
    enabled: bool,
    created_by: str,
) -> list[str | None]:
    """One cron per schedule trigger of an enabled automation; all or nothing."""
    cron_ids: list[str | None] = []
    try:
        for trigger, trigger_id in zip(triggers, trigger_ids, strict=True):
            if enabled and isinstance(trigger, ScheduleTrigger):
                cron_ids.append(
                    await _create_cron(automation_id, trigger_id, trigger.cron, created_by)
                )
            else:
                cron_ids.append(None)
    except Exception as exc:
        for cron_id in cron_ids:
            await _delete_cron(cron_id)
        logger.exception("Failed to create schedule cron", extra={"schedule_id": automation_id})
        raise HTTPException(502, "failed to create schedule cron") from exc
    return cron_ids


def _parsed_triggers(record: dict[str, Any]) -> list[TriggerConfig]:
    return _TRIGGERS.validate_python([t["config"] for t in record.get("triggers") or []])


async def _checked_triggers(
    triggers: Sequence[TriggerConfig], login: str, *, use_workspace_credentials: bool
) -> list[TriggerConfig]:
    """``triggers`` once whoever configures them can reach every repository they name,
    and Open SWE can read every Slack channel they watch."""
    repos = {trigger.repo for trigger in triggers if isinstance(trigger, GitHubTrigger)}
    for repo in repos:
        if use_workspace_credentials:
            await repo_config_for_workspace(repo)
        else:
            await repo_config_for_user(login, repo)
    for channel in sorted({t.channel for t in triggers if isinstance(t, SlackTrigger)}):
        await _require_watchable_slack_channel(channel)
    return list(triggers)


async def _require_watchable_slack_channel(channel_id: str) -> None:
    """Refuse a channel whose messages Slack doesn't deliver to Open SWE."""
    channel = await SlackChannel.load(channel_id, use_cache=False)
    if channel is None:
        raise HTTPException(422, f"Slack channel {channel_id} could not be read")
    if not channel.context.allows_operations:
        raise HTTPException(422, "externally shared Slack channels can't trigger automations")
    if channel.payload.get("is_member") is not True:
        raise HTTPException(
            422, f"invite Open SWE to #{channel.name or channel_id} so it sees its messages"
        )


async def _refuse_public_events_for_admin(
    triggers: Sequence[TriggerConfig], *, admin_thread: bool
) -> None:
    """An admin-thread automation never runs on events in a public repository.

    Anyone can write the issue or pull request text such an event carries, and
    an admin thread holds workspace-admin tools.
    """
    if not admin_thread:
        return
    for repo in sorted({t.repo for t in triggers if isinstance(t, GitHubTrigger)}):
        if await repo_is_private(repo) is not True:
            raise HTTPException(
                422,
                f"admin-thread automations can't run on GitHub events in {repo}: it is public, "
                "or its visibility could not be checked",
            )


def _repo_dict(full_name: str | None) -> dict[str, str] | None:
    if not full_name or "/" not in full_name:
        return None
    owner, name = full_name.split("/", 1)
    return {"owner": owner, "name": name}


def _test_run_repo(record: dict[str, Any]) -> str | None:
    """The repository a test run starts in: its first GitHub trigger's, which events would use."""
    for trigger in record.get("triggers") or []:
        repo = (trigger.get("config") or {}).get("repo")
        if trigger.get("kind") == "github" and isinstance(repo, str) and repo:
            return repo
    return None


def _report_to_slack(record: dict[str, Any], prompt_text: str) -> str:
    """``prompt_text`` asking for the Slack report a stored destination used to post."""
    channel = record.get("slack_channel_id")
    if not isinstance(channel, str) or not channel.upper().startswith(("C", "G")):
        return prompt_text
    when = (
        "When a run takes a concrete action, post"
        if record.get("slack_notification_mode") == "on_action"
        else "Post"
    )
    return f"{prompt_text}\n\n{when} a short summary of the outcome to <#{channel.upper()}>."


def _work_in_repo(prompt_text: str, repo: str | None) -> str:
    """``prompt_text`` naming ``repo``, for a schedule that used to start runs there."""
    return f"{prompt_text}\n\nScheduled runs work in `{repo}`." if repo else prompt_text


async def create_agent_schedule(
    login: str,
    body: ScheduleCreateBody,
    *,
    email: str | None = None,
    allow_admin_thread: bool = False,
    use_workspace_credentials: bool = False,
) -> dict[str, Any]:
    if body.admin_thread and not allow_admin_thread:
        raise HTTPException(403, "admin only")
    if use_workspace_credentials:
        profile: dict[str, Any] = {}
        run_email = email
    else:
        await _ensure_dashboard_github_token(login)
        profile = await get_profile(login) or {}
        run_email = await resolve_run_email(login, profile) or email
    triggers = await _checked_triggers(
        body.triggers, login, use_workspace_credentials=use_workspace_credentials
    )
    await _refuse_public_events_for_admin(triggers, admin_thread=body.admin_thread)
    workspace = await _existing_workspace(body.workspace)
    workspace_id = await WORKSPACES.id_for_slug(workspace)
    if workspace_id is None:
        raise HTTPException(422, f"no workspace named {workspace!r}")
    chosen_model, chosen_effort = normalize_model_choice(body.model_id, body.effort)
    automation_id = str(uuid.uuid4())
    trigger_ids = [uuid.uuid4() for _ in triggers]
    cron_ids = await _create_crons(
        automation_id, triggers, trigger_ids, enabled=True, created_by=login
    )
    try:
        async with transaction() as conn:
            await conn.execute(
                text(
                    "INSERT INTO automation (id, workspace_id, name, prompt, admin_thread, "
                    "model, effort, base_branch, branch_prefix, enabled, created_by, "
                    "updated_by, user_email) VALUES (:id, :workspace_id, :name, :prompt, "
                    ":admin_thread, :model, :effort, :base_branch, :branch_prefix, true, "
                    ":login, :login, :user_email)"
                ),
                {
                    "id": uuid.UUID(automation_id),
                    "workspace_id": workspace_id,
                    "name": (body.name or _derive_name(body.prompt)).strip(),
                    "prompt": body.prompt.strip(),
                    "admin_thread": body.admin_thread,
                    "model": chosen_model or profile.get("default_model") or "Default",
                    "effort": chosen_effort or profile.get("reasoning_effort"),
                    "base_branch": profile.get("base_branch") or "main",
                    "branch_prefix": profile.get("branch_prefix"),
                    "login": login,
                    "user_email": (run_email or "").strip().lower(),
                },
            )
            await _insert_triggers(conn, automation_id, triggers, trigger_ids, cron_ids)
    except Exception:
        for cron_id in cron_ids:
            await _delete_cron(cron_id)
        raise
    record = await get_agent_schedule(automation_id)
    assert record is not None
    return _schedule_summary(record)


async def update_agent_schedule(
    schedule_id: str,
    login: str,
    body: ScheduleUpdateBody,
    *,
    email: str | None = None,
    allow_admin_thread: bool = False,
    use_workspace_credentials: bool = False,
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

    columns: dict[str, object] = {"updated_by": login}
    if body.prompt is not None:
        columns["prompt"] = body.prompt.strip()
    if body.name is not None:
        columns["name"] = body.name.strip() or _derive_name(
            str(columns.get("prompt", existing["prompt"]))
        )
    if body.model_id is not None or body.effort is not None:
        model, effort = normalize_model_choice(body.model_id, body.effort)
        if model and effort:
            columns["model"] = model
            columns["effort"] = effort
    if body.enabled is not None:
        columns["enabled"] = body.enabled
    if body.admin_thread is not None:
        columns["admin_thread"] = body.admin_thread
    if body.workspace is not None:
        workspace = await _existing_workspace(body.workspace)
        columns["workspace_id"] = await WORKSPACES.id_for_slug(workspace)

    current_triggers = _parsed_triggers(existing)
    triggers = current_triggers
    if body.triggers is not None and [t.model_dump() for t in body.triggers] != [
        t.model_dump() for t in current_triggers
    ]:
        triggers = await _checked_triggers(
            body.triggers,
            existing["created_by"],
            use_workspace_credentials=use_workspace_credentials,
        )

    was_admin = existing.get("admin_thread") is True
    admin_thread = body.admin_thread if body.admin_thread is not None else was_admin
    # Only an edit that could newly put admin runs on a repository's events is
    # checked; pausing or renaming must keep working if GitHub can't be read,
    # and launches skip repositories that went public since.
    if triggers is not current_triggers or (admin_thread and not was_admin):
        await _refuse_public_events_for_admin(triggers, admin_thread=admin_thread)

    enabled = bool(columns.get("enabled", existing.get("enabled")))
    rebuild_triggers = triggers is not current_triggers or enabled != bool(existing.get("enabled"))
    old_cron_ids = [t.get("cron_id") for t in existing.get("triggers") or []]
    trigger_ids = [uuid.uuid4() for _ in triggers]
    new_cron_ids: list[str | None] = []
    if rebuild_triggers:
        new_cron_ids = await _create_crons(
            existing["id"],
            triggers,
            trigger_ids,
            enabled=enabled,
            created_by=existing["created_by"],
        )
    assignments = ", ".join(f"{key} = :{key}" for key in columns)
    try:
        async with transaction() as conn:
            await conn.execute(
                text(
                    f"UPDATE automation SET {assignments}, updated_at = clock_timestamp() "
                    "WHERE id = :id"
                ),
                {**columns, "id": uuid.UUID(existing["id"])},
            )
            if rebuild_triggers:
                await conn.execute(
                    text("DELETE FROM automation_trigger WHERE automation_id = :id"),
                    {"id": uuid.UUID(existing["id"])},
                )
                await _insert_triggers(conn, existing["id"], triggers, trigger_ids, new_cron_ids)
    except Exception:
        for cron_id in new_cron_ids:
            await _delete_cron(cron_id)
        raise
    if rebuild_triggers:
        for cron_id in old_cron_ids:
            await _delete_cron(cron_id)
    updated = await get_agent_schedule(existing["id"])
    assert updated is not None
    return _schedule_summary(updated)


async def delete_agent_schedule(schedule_id: str) -> None:
    existing = await get_agent_schedule(schedule_id)
    _assert_schedule_exists(existing)
    assert existing is not None
    for trigger in existing.get("triggers") or []:
        await _delete_cron(trigger.get("cron_id"))
    async with transaction() as conn:
        await conn.execute(
            text("DELETE FROM automation WHERE id = :id"), {"id": uuid.UUID(existing["id"])}
        )


async def delete_workspace_automations(workspace: str) -> int:
    """Delete a workspace's automations and their crons; returns how many.

    Runs before the workspace row goes, which would cascade the rows anyway:
    the crons live in the LangGraph API, outside that transaction.
    """
    async with transaction() as conn:
        records = await _load_records(conn, "WHERE w.slug = :slug", {"slug": workspace})
    for record in records:
        for trigger in record.get("triggers") or []:
            await _delete_cron(trigger.get("cron_id"))
    async with transaction() as conn:
        await conn.execute(
            text(
                "DELETE FROM automation WHERE workspace_id = "
                "(SELECT id FROM workspace WHERE slug = :slug)"
            ),
            {"slug": workspace},
        )
    return len(records)


def _legacy_triggers(record: dict[str, Any]) -> tuple[list[TriggerConfig], str | None]:
    trigger = record.get("trigger") or "schedule"
    repo = _repo_full_name(record.get("repo") if isinstance(record.get("repo"), dict) else None)
    if trigger == "schedule":
        schedule = record.get("schedule")
        return ([ScheduleTrigger(cron=schedule)] if isinstance(schedule, str) else []), (
            record.get("cron_id") if isinstance(record.get("cron_id"), str) else None
        )
    # Older releases only had this one event trigger, which always named a repo.
    if trigger == "github_issue_opened" and repo:
        return [GitHubTrigger(repo=repo, events=["issues.opened"])], None
    return [], None


async def _import_store_automation(
    record: dict[str, Any], run_states: dict[str, dict[str, Any]]
) -> bool:
    """Copy one stored automation into PostgreSQL; ``True`` when this call inserted it."""
    imported = False
    schedule_id = record.get("id")
    if not isinstance(schedule_id, str) or _uuid(schedule_id) is None:
        logger.error("Skipping an unreadable stored automation", extra={"schedule_id": schedule_id})
        return False
    workspace = _record_workspace(record)
    workspace_id = await WORKSPACES.id_for_slug(workspace)
    triggers, cron_id = _legacy_triggers(record)
    if workspace_id is None or not triggers:
        logger.warning(
            "Dropping a stored automation that cannot be imported",
            extra={"schedule_id": schedule_id, "workspace": workspace},
        )
        await _delete_cron(cron_id)
        await delete_value(SCHEDULES_NAMESPACE, schedule_id)
        await delete_value(SCHEDULE_RUN_STATE_NAMESPACE, schedule_id)
        return False
    state = {**record, **run_states.get(schedule_id, {})}
    async with transaction() as conn:
        inserted = await conn.execute(
            text(
                "INSERT INTO automation (id, workspace_id, name, prompt, admin_thread, model, "
                "effort, base_branch, branch_prefix, enabled, created_by, updated_by, "
                "user_email, last_thread_id, last_run_id, last_triggered_at, last_error, "
                "last_error_at) VALUES (:id, "
                ":workspace_id, :name, :prompt, :admin_thread, :model, :effort, :base_branch, "
                ":branch_prefix, :enabled, :created_by, :updated_by, :user_email, "
                ":last_thread_id, :last_run_id, :last_triggered_at, :last_error, "
                ":last_error_at) ON CONFLICT (id) DO NOTHING "
                "RETURNING 1"
            ),
            {
                "id": uuid.UUID(schedule_id),
                "workspace_id": workspace_id,
                "name": str(record.get("name") or _derive_name(str(record.get("prompt") or ""))),
                # Schedules no longer name a repository and automations no longer
                # post to Slack themselves; the prompt keeps both.
                "prompt": _report_to_slack(
                    record,
                    _work_in_repo(
                        str(record.get("prompt") or ""),
                        None
                        if record.get("trigger") not in (None, "schedule")
                        else _repo_full_name(
                            record.get("repo") if isinstance(record.get("repo"), dict) else None
                        ),
                    ),
                ),
                "admin_thread": record.get("admin_thread") is True,
                "model": record.get("model") or "Default",
                "effort": record.get("effort"),
                "base_branch": record.get("base_branch") or "main",
                "branch_prefix": record.get("branch_prefix"),
                "enabled": bool(record.get("enabled")),
                "created_by": record.get("created_by") or "",
                "updated_by": record.get("updated_by") or record.get("created_by") or "",
                "user_email": record.get("user_email") or "",
                "last_thread_id": state.get("last_thread_id"),
                "last_run_id": state.get("last_run_id"),
                "last_triggered_at": _timestamp(state.get("last_triggered_at")),
                "last_error": state.get("last_error"),
                "last_error_at": _timestamp(state.get("last_error_at")),
            },
        )
        if inserted.first() is not None:
            await _insert_triggers(
                conn,
                schedule_id,
                triggers,
                [uuid.uuid4() for _ in triggers],
                [cron_id if isinstance(t, ScheduleTrigger) else None for t in triggers],
            )
            imported = True
    await delete_value(SCHEDULES_NAMESPACE, schedule_id)
    await delete_value(SCHEDULE_RUN_STATE_NAMESPACE, schedule_id)
    return imported


async def import_store_automations() -> int:
    """Copy automations still in the LangGraph Store into PostgreSQL; returns how many.

    Runs at startup and is a no-op once the Store namespaces are empty. Ids are
    kept, so existing crons keep pointing at their automation. A record whose
    workspace no longer exists is dropped along with its cron; one that cannot
    be read stays in the Store, logged, for the next startup.
    """
    run_states = {
        state["schedule_id"]: state
        for state in await search_all_values(SCHEDULE_RUN_STATE_NAMESPACE)
        if isinstance(state.get("schedule_id"), str)
    }
    imported = 0
    for record in await search_all_values(SCHEDULES_NAMESPACE):
        try:
            imported += await _import_store_automation(record, run_states)
        except Exception:
            # One bad record must not keep every later one in the Store.
            logger.exception(
                "Could not import a stored automation; it stays in the Store",
                extra={"schedule_id": record.get("id")},
            )
    return imported


def _admin_thread_enabled(record: dict[str, Any]) -> bool:
    email = record.get("user_email")
    login = record.get("created_by")
    return record.get("admin_thread") is True and is_admin(
        email if isinstance(email, str) else None,
        login=login if isinstance(login, str) else None,
    )


async def authorized_admin_schedule(cfg: RunConfig) -> dict[str, Any] | None:
    """Verify a system admin grant against its saved invocation and current schedule."""
    if (
        cfg.source != "schedule"
        or cfg.admin_thread is not True
        or not cfg.thread_id
        or not cfg.invocation_id
        or cfg.github_login
        or cfg.user_email
    ):
        return None
    metadata = thread_metadata(await langgraph_client().threads.get(cfg.thread_id))
    if metadata.get("owner_type") != "system" or metadata.get("visibility") != "public":
        return None
    grant = metadata.get("system_authorization")
    if (
        not isinstance(grant, dict)
        or grant.get("invocation_id") != cfg.invocation_id
        or not isinstance(schedule_id := grant.get("schedule_id"), str)
        or not schedule_id
        or schedule_id != cfg.schedule_id
    ):
        return None
    record = await get_agent_schedule(schedule_id)
    return record if record and _admin_thread_enabled(record) else None


def _agent_run_metadata(
    record: dict[str, Any],
    thread_id: str,
    repo: dict[str, str] | None,
    *,
    test_run: bool = False,
    admin_thread: bool = False,
) -> dict[str, Any]:
    created_ms = now_ms()
    title_prefix = "Test" if test_run else "Scheduled"
    metadata: dict[str, Any] = {
        "source": "schedule",
        "origin": "schedule",
        "thread_category": "automation",
        "trigger_kind": "schedule_test" if test_run else "schedule",
        "schedule_id": record["id"],
        "automation_scope": "workspace",
        "workspace": _record_workspace(record),
        "owner_type": "system",
        "visibility": "public",
        "schedule_name": record.get("name"),
        "schedule_test": test_run,
        "created_by": record.get("created_by"),
        "title": f"{title_prefix}: {record.get('name') or 'Agent'}",
        "base_branch": record.get("base_branch") or "main",
        "branch_prefix": record.get("branch_prefix"),
        "model": record.get("model") or "Default",
        "effort": record.get("effort"),
        "created_at_ms": created_ms,
        "updated_at_ms": created_ms,
    }
    if repo:
        metadata["repo_owner"] = repo["owner"]
        metadata["repo_name"] = repo["name"]
    if admin_thread:
        metadata["admin_thread"] = True
    return metadata


async def _agent_run_config(
    record: dict[str, Any],
    thread_id: str,
    repo: dict[str, str] | None,
    *,
    test_run: bool = False,
    admin_thread: bool = False,
) -> dict[str, Any]:
    configurable = with_invocation_id(
        {
            "thread_id": thread_id,
            "source": "schedule",
            "schedule_id": record["id"],
            "schedule_test": test_run,
        },
        new_invocation_id(),
    )
    if repo:
        configurable["repo"] = repo
    workspace = _record_workspace(record)
    configurable["workspace"] = workspace
    configurable["environment"] = workspace
    if admin_thread:
        configurable["admin_thread"] = True
    model, effort = normalize_model_choice(record.get("model"), record.get("effort"))
    if model and effort:
        model, effort = gate_fable_model(
            model, effort, fable_enabled=(await get_workspace_settings(workspace)).fable_enabled
        )
        configurable["agent_model_id"] = model
        configurable["agent_effort"] = effort
    return {"configurable": configurable, "metadata": agent_version_metadata()}


async def _launch_agent_schedule_record(
    record: dict[str, Any],
    *,
    repo: str | None,
    test_run: bool = False,
    prompt: str | None = None,
    token_repositories: list[str] | None = None,
) -> dict[str, Any]:
    schedule_id = record["id"]
    if not test_run and not record.get("enabled"):
        return {"status": "disabled", "schedule_id": schedule_id}

    workspace = _record_workspace(record)
    if not await WORKSPACES.slug_exists(workspace):
        # Running in another workspace would hand it that workspace's settings
        # and connections, which nobody chose for it.
        error = f"workspace {workspace!r} no longer exists"
        await _put_run_state(record, {"last_error": error, "last_error_at": now_iso()})
        return {"status": "unknown_workspace", "schedule_id": schedule_id, "error": error}

    if repo:
        try:
            await require_repo_access_for_workspace(repo)
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
    thread_id = str(uuid.uuid4())
    admin_thread = _admin_thread_enabled(record)
    repo_config = _repo_dict(repo)
    run_config = await _agent_run_config(
        record, thread_id, repo_config, test_run=test_run, admin_thread=admin_thread
    )
    metadata = _agent_run_metadata(
        record, thread_id, repo_config, test_run=test_run, admin_thread=admin_thread
    )
    if token_repositories is not None:
        metadata[GITHUB_TOKEN_REPOSITORIES_KEY] = token_repositories
    if admin_thread:
        metadata["system_authorization"] = {
            "schedule_id": schedule_id,
            "invocation_id": run_config["configurable"]["invocation_id"],
        }
    await create_thread(
        client, thread_id, title=metadata["title"], metadata=metadata, if_exists="do_nothing"
    )
    await client.threads.update(thread_id=thread_id, metadata=metadata)
    input_context: InputMessageContext = {
        "sender_id": f"system:schedule:{schedule_id}",
        "surface": "automation",
        "kind": "system",
    }
    run = await create_durable_run(
        thread_id,
        _AGENT_ASSISTANT_ID,
        input=build_run_input(
            str(record["prompt"]) if prompt is None else prompt,
            input_context,
            systems=[
                {
                    "id": f"system:schedule:{schedule_id}",
                    "display_name": record.get("name") or "Scheduled automation",
                    "platform": "open-swe",
                }
            ],
        ),
        source="schedule",
        thread_title=None,
        config=run_config,
        client=client,
        stream_resumable=True,
    )
    run_id = run.get("run_id") if isinstance(run, dict) else getattr(run, "run_id", None)
    # The run is durable now; bookkeeping failures must not release delivery claims.
    log_context = {"schedule_id": schedule_id, "thread_id": thread_id, "run_id": run_id}
    try:
        await client.threads.update(
            thread_id=thread_id,
            metadata={
                "latest_run_id": run_id,
                "latest_run_status": "pending",
                "updated_at_ms": now_ms(),
            },
        )
    except Exception:
        logger.exception("Failed to save dispatched automation thread metadata", extra=log_context)
    try:
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
    except Exception:
        logger.exception("Failed to save dispatched automation run state", extra=log_context)
    return {
        "status": "started",
        "schedule_id": schedule_id,
        "thread_id": thread_id,
        "run_id": run_id,
    }


def _github_events(event_type: str, payload: dict[str, Any]) -> set[GitHubEvent]:
    """The trigger events one GitHub delivery stands for."""
    action = payload.get("action")
    if event_type == "issues" and action == "opened":
        return {"issues.opened"}
    if event_type != "pull_request":
        return set()
    if action == "opened":
        return {"pull_request.opened"}
    if action == "closed":
        pull_request = payload.get("pull_request")
        merged = isinstance(pull_request, dict) and pull_request.get("merged") is True
        return {"pull_request.closed", "pull_request.merged"} if merged else {"pull_request.closed"}
    return set()


async def _github_event_prompt(
    record: dict[str, Any], event_type: str, payload: dict[str, Any], event: GitHubEvent
) -> str:
    subject_value = payload.get("issue" if event_type == "issues" else "pull_request")
    subject: dict[str, Any] = subject_value if isinstance(subject_value, dict) else {}
    author_value = subject.get("user")
    author: dict[str, Any] = author_value if isinstance(author_value, dict) else {}
    login = str(author.get("login") or "")
    lines = [
        f"{'Issue' if event_type == 'issues' else 'Pull request'}: "
        f"#{subject.get('number', '')} {subject.get('title', '')}",
        f"URL: {subject.get('html_url', '')}",
        f"Author: {login}",
    ]
    if event_type == "pull_request":
        base_value, head_value = subject.get("base"), subject.get("head")
        base: dict[str, Any] = base_value if isinstance(base_value, dict) else {}
        head: dict[str, Any] = head_value if isinstance(head_value, dict) else {}
        lines += [
            f"Base: {base.get('ref', '')}  Head: {head.get('ref', '')}",
            f"Merged: {'yes' if subject.get('merged') is True else 'no'}",
        ]
    context = "\n".join(lines) + f"\n\n{subject.get('body') or ''}"
    registered = bool(await User.known_logins([login]))
    return prompt(
        "runs/github-automation-event",
        prompt=record["prompt"],
        event_description=GITHUB_EVENT_DESCRIPTIONS[event],
        event_context=fence_github_comment_body(context, registered=registered),
    )


async def _github_trigger_matches(
    repo_full_name: str, events: set[GitHubEvent]
) -> list[tuple[dict[str, Any], GitHubEvent]]:
    """Enabled automations with a GitHub trigger on this repository for one of ``events``."""
    async with transaction() as conn:
        records = await _load_records(
            conn,
            "WHERE a.enabled AND a.id IN (SELECT automation_id FROM automation_trigger "
            "WHERE kind = 'github' AND match_key = :repo)",
            {"repo": repo_full_name},
        )
    matches: list[tuple[dict[str, Any], GitHubEvent]] = []
    for record in records:
        # Only this repository's triggers: another GitHub trigger on the same
        # automation configures events for its own repository.
        configured = {
            event
            for trigger in record.get("triggers") or []
            if trigger.get("kind") == "github"
            and str((trigger.get("config") or {}).get("repo") or "").lower() == repo_full_name
            for event in (trigger.get("config") or {}).get("events") or []
        }
        fired = sorted(event for event in events if event in configured)
        if fired:
            # The most specific event names the run: a merge over a plain close.
            matches.append(
                (record, "pull_request.merged" if "pull_request.merged" in fired else fired[0])
            )
    return matches


async def launch_github_automations(
    event_type: str, payload: dict[str, Any], delivery_id: str
) -> list[dict[str, Any]]:
    """Run every automation a GitHub delivery triggers, each at most once per delivery."""
    events = _github_events(event_type, payload)
    if not events:
        return []
    sender_value = payload.get("sender")
    sender_login = sender_value.get("login") if isinstance(sender_value, dict) else None
    sender = sender_login.lower() if isinstance(sender_login, str) else ""
    if sender in OPEN_SWE_GITHUB_LOGINS:
        # A run that opens or closes a pull request would otherwise trigger itself.
        logger.info(
            "Ignoring Open SWE's own GitHub event for automations",
            extra={"github_delivery": delivery_id, "github_event": event_type},
        )
        return []
    if not delivery_id:
        logger.warning("GitHub automation delivery is missing a delivery ID")
        return []
    repo_value = payload.get("repository")
    repo: dict[str, Any] = repo_value if isinstance(repo_value, dict) else {}
    owner_value = repo.get("owner")
    owner: dict[str, Any] = owner_value if isinstance(owner_value, dict) else {}
    owner_login = owner.get("login")
    repo_name = repo.get("name")
    if (
        not isinstance(owner_login, str)
        or not owner_login
        or not isinstance(repo_name, str)
        or not repo_name
    ):
        logger.error(
            "GitHub automation payload is missing repository identity",
            extra={"github_delivery": delivery_id},
        )
        return []
    full_name = f"{owner_login}/{repo_name}".lower()
    matches = await _github_trigger_matches(full_name, events)
    if not matches:
        logger.info(
            "No GitHub automations matched the delivery",
            extra={"github_delivery": delivery_id, "github_repo": full_name},
        )
        return []
    # Pull request events on a public repository run only for org members; issue
    # automations keep firing for any author, as before, with a narrowed token.
    if event_type == "pull_request" and await enforce_public_repo_org_gate(payload, event_type):
        return []
    results: list[dict[str, Any]] = []
    private = repo_private_from_payload(payload)
    for record, event in matches:
        schedule_id = record["id"]
        if record.get("admin_thread") is True and private is not True:
            # Saving refuses this, but a repository can go public afterwards.
            logger.warning(
                "Skipping an admin-thread automation for a public repository event",
                extra={"schedule_id": schedule_id, "github_delivery": delivery_id},
            )
            continue
        claim_key = f"{schedule_id}:{delivery_id}"
        try:
            claimed = await event_claims.claim(
                _DELIVERY_CLAIM_SCOPE, claim_key, ttl=_DELIVERY_CLAIM_TTL
            )
        except Exception:
            logger.exception(
                "Failed to claim GitHub automation delivery",
                extra={"schedule_id": schedule_id, "github_delivery": delivery_id},
            )
            continue
        if not claimed:
            logger.info(
                "Skipping already-claimed GitHub automation delivery",
                extra={"schedule_id": schedule_id, "github_delivery": delivery_id},
            )
            continue
        try:
            result = await _launch_agent_schedule_record(
                record,
                repo=f"{owner_login}/{repo_name}",
                prompt=await _github_event_prompt(record, event_type, payload, event),
                # An outsider can open an issue on a public repository, so the
                # run it starts reaches only that repository.
                token_repositories=event_token_repositories(
                    owner_login, repo_name, private=private
                ),
            )
        except Exception:
            logger.exception(
                "Failed to launch GitHub automation", extra={"schedule_id": schedule_id}
            )
            await event_claims.release(_DELIVERY_CLAIM_SCOPE, claim_key)
            continue
        if result.get("status") != "started":
            logger.error(
                "GitHub automation did not start",
                extra={
                    "schedule_id": schedule_id,
                    "github_delivery": delivery_id,
                    "launch_status": result.get("status"),
                },
            )
            await event_claims.release(_DELIVERY_CLAIM_SCOPE, claim_key)
        results.append(result)
    return results


async def launch_github_issue_automations(
    payload: dict[str, Any], delivery_id: str
) -> list[dict[str, Any]]:
    return await launch_github_automations("issues", {"action": "opened", **payload}, delivery_id)


_SLACK_FENCE_RE = re.compile(r"<(\s*/?\s*untrusted_slack_message)", re.IGNORECASE)
# Message subtypes that are someone posting, as opposed to edits, joins, or deletions.
_SLACK_POST_SUBTYPES = frozenset({"", "bot_message", "file_share"})


def _slack_message_text(event: SlackEvent) -> str:
    """The text a message shows, with alert attachments (where bots put their content)."""
    parts = [event.text or ""]
    for attachment in event.attachments:
        for field in ("pretext", "title", "text", "fallback"):
            value = attachment.get(field)
            if isinstance(value, str) and value.strip() and value not in parts:
                parts.append(value)
    return "\n".join(part for part in parts if part.strip())


def _slack_trigger_fires(
    config: dict[str, Any], channel: str, *, from_bot: bool, text: str
) -> bool:
    if config.get("channel") != channel or "message.posted" not in (config.get("events") or []):
        return False
    senders = config.get("senders") or "anyone"
    if (senders == "bots" and not from_bot) or (senders == "people" and from_bot):
        return False
    pattern = config.get("match")
    return not pattern or re.search(pattern, text, re.IGNORECASE) is not None


def _slack_event_prompt(
    record: dict[str, Any], channel: SlackChannelContext, event: SlackEvent, text: str
) -> str:
    author = f"bot {event.bot_id}" if event.is_from_bot else f"<@{event.resolve_user_id()}>"
    message = text[:_SLACK_MESSAGE_MAX_CHARS]
    return prompt(
        "runs/slack-automation-event",
        prompt=record["prompt"],
        event_description=SLACK_EVENT_DESCRIPTIONS["message.posted"],
        channel=f"#{channel.label}" if channel.label else channel.id,
        channel_id=channel.id,
        author=author,
        ts=event.ts,
        message=_SLACK_FENCE_RE.sub(r"&lt;\1", message),
    )


async def _claim_delivery(
    claim_key: str, group: str, max_runs_per_hour: object, schedule_id: str
) -> bool:
    """Claim a delivery for one automation, within its trigger's hourly run cap."""
    if not isinstance(max_runs_per_hour, int):
        return await event_claims.claim(_DELIVERY_CLAIM_SCOPE, claim_key, ttl=_DELIVERY_CLAIM_TTL)
    outcome = await event_claims.claim_within_limit(
        _DELIVERY_CLAIM_SCOPE,
        claim_key,
        ttl=_DELIVERY_CLAIM_TTL,
        group=group,
        limit=max_runs_per_hour,
        within=_RATE_WINDOW,
    )
    if outcome == "limited":
        logger.warning(
            "Automation trigger hit its hourly run limit",
            extra={"schedule_id": schedule_id, "claim_group": group},
        )
    return outcome == "claimed"


async def slack_channel_watched(channel_id: str) -> bool:
    """Whether an enabled automation has a Slack trigger on ``channel_id``."""
    if not postgres.configured():
        return False
    try:
        async with transaction() as conn:
            row = await conn.execute(
                text(
                    "SELECT 1 FROM automation_trigger t JOIN automation a "
                    "ON a.id = t.automation_id WHERE t.kind = 'slack' "
                    "AND t.match_key = :channel AND a.enabled LIMIT 1"
                ),
                {"channel": channel_id.upper()},
            )
            return row.first() is not None
    except Exception:
        logger.exception(
            "Could not check for Slack automations", extra={"slack_channel": channel_id}
        )
        return False


async def launch_slack_automations(
    envelope: SlackEventEnvelope, channel: SlackChannelContext, bot_user_id: str
) -> list[dict[str, Any]]:
    """Run every automation a posted Slack message triggers, each at most once per message."""
    event = envelope.event
    if (
        event is None
        or event.type != "message"
        or event.subtype not in _SLACK_POST_SUBTYPES
        or (event.thread_ts and event.thread_ts != event.ts)
        or not event.ts
    ):
        return []
    channel_id = event.resolve_channel_id().upper()
    if not _SLACK_TRIGGER_CHANNEL_RE.fullmatch(channel_id) or not channel.allows_operations:
        return []
    user = event.resolve_user_id()
    if (bot_user_id and user == bot_user_id) or (
        event.app_id and event.app_id == envelope.api_app_id
    ):
        return []
    async with transaction() as conn:
        records = await _load_records(
            conn,
            "WHERE a.enabled AND a.id IN (SELECT automation_id FROM automation_trigger "
            "WHERE kind = 'slack' AND match_key = :channel)",
            {"channel": channel_id},
        )
    text = _slack_message_text(event)
    results: list[dict[str, Any]] = []
    for record in records:
        schedule_id = record["id"]
        fired = next(
            (
                trigger.get("config") or {}
                for trigger in record.get("triggers") or []
                if trigger.get("kind") == "slack"
                and _slack_trigger_fires(
                    trigger.get("config") or {},
                    channel_id,
                    from_bot=event.is_from_bot,
                    text=text,
                )
            ),
            None,
        )
        if fired is None:
            continue
        claim_prefix = f"{schedule_id}:{channel_id}:"
        claim_key = f"{claim_prefix}{event.ts}"
        if not await _claim_delivery(
            claim_key, claim_prefix, fired.get("max_runs_per_hour"), schedule_id
        ):
            continue
        try:
            result = await _launch_agent_schedule_record(
                record, repo=None, prompt=_slack_event_prompt(record, channel, event, text)
            )
        except Exception:
            logger.exception(
                "Failed to launch Slack automation", extra={"schedule_id": schedule_id}
            )
            await event_claims.release(_DELIVERY_CLAIM_SCOPE, claim_key)
            continue
        if result.get("status") != "started":
            logger.error(
                "Slack automation did not start",
                extra={"schedule_id": schedule_id, "launch_status": result.get("status")},
            )
            await event_claims.release(_DELIVERY_CLAIM_SCOPE, claim_key)
        results.append(result)
    return results


def _named(items: object) -> dict[str, str]:
    """``{id: name}`` for a Linear list of ``{id, name}`` objects."""
    if not isinstance(items, list):
        return {}
    return {
        str(item["id"]): str(item.get("name") or "")
        for item in items
        if isinstance(item, dict) and item.get("id")
    }


def _linear_issue_events(payload: dict[str, Any]) -> tuple[set[LinearTriggerEvent], set[str]]:
    """The trigger events one Linear delivery stands for, and the label names it added."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if payload.get("type") != "Issue" or not isinstance(data, dict):
        return set(), set()
    if payload.get("action") == "create":
        return {"issue.created"}, set()
    updated_from = payload.get("updatedFrom")
    if payload.get("action") != "update" or not isinstance(updated_from, dict):
        return set(), set()
    before = updated_from.get("labelIds")
    after = data.get("labelIds")
    if not isinstance(before, list) or not isinstance(after, list):
        return set(), set()
    added = {str(label) for label in after} - {str(label) for label in before}
    names = _named(data.get("labels"))
    added_names = {names.get(label_id, "") for label_id in added} - {""}
    return ({"issue.labeled"}, added_names) if added else (set(), set())


def _linear_team_key(data: dict[str, Any]) -> str:
    team = data.get("team")
    if isinstance(team, dict) and isinstance(team.get("key"), str) and team["key"]:
        return team["key"].upper()
    identifier = data.get("identifier")
    if isinstance(identifier, str) and "-" in identifier:
        return identifier.rsplit("-", 1)[0].upper()
    return ""


def _linear_trigger_fires(
    config: dict[str, Any],
    team: str,
    event: LinearTriggerEvent,
    *,
    labels: set[str],
    added_labels: set[str],
    project: str,
) -> bool:
    if config.get("team") != team or event not in (config.get("events") or []):
        return False
    wanted_project = config.get("project")
    if wanted_project and wanted_project.lower() != project.lower():
        return False
    wanted = {label.lower() for label in config.get("labels") or []}
    if not wanted:
        return True
    candidates = added_labels if event == "issue.labeled" else labels
    return bool(wanted & {label.lower() for label in candidates})


async def _linear_event_prompt(
    record: dict[str, Any],
    data: dict[str, Any],
    event: LinearTriggerEvent,
    *,
    team: str,
    labels: set[str],
    project: str,
) -> str:
    creator = data.get("creator") if isinstance(data.get("creator"), dict) else {}
    creator_email = creator.get("email") if isinstance(creator, dict) else None
    state = data.get("state") if isinstance(data.get("state"), dict) else {}
    lines = [
        f"Issue: {data.get('identifier') or ''} {data.get('title') or ''}",
        f"URL: {data.get('url') or ''}",
        f"Team: {team}",
        f"Project: {project or '(none)'}",
        f"Labels: {', '.join(sorted(labels)) or '(none)'}",
        f"State: {state.get('name') if isinstance(state, dict) else ''}",
        f"Creator: {creator.get('name') if isinstance(creator, dict) else ''}",
    ]
    description = str(data.get("description") or "")[:_LINEAR_DESCRIPTION_MAX_CHARS]
    context = "\n".join(lines) + f"\n\n{description}"
    registered = bool(await User.login_for_email(creator_email))
    return prompt(
        "runs/linear-automation-event",
        prompt=record["prompt"],
        event_description=LINEAR_EVENT_DESCRIPTIONS[event],
        event_context=fence_github_comment_body(context, registered=registered),
    )


async def launch_linear_automations(
    payload: dict[str, Any], delivery_id: str
) -> list[dict[str, Any]]:
    """Run every automation a Linear issue delivery triggers, each at most once per delivery."""
    events, added_labels = _linear_issue_events(payload)
    if not events:
        return []
    if not delivery_id:
        logger.warning("Linear automation delivery is missing a delivery ID")
        return []
    data: dict[str, Any] = payload["data"]
    team = _linear_team_key(data)
    if not _LINEAR_TEAM_KEY_RE.fullmatch(team):
        return []
    labels = set(_named(data.get("labels")).values()) - {""}
    project_value = data.get("project")
    project = str(project_value.get("name") or "") if isinstance(project_value, dict) else ""
    async with transaction() as conn:
        records = await _load_records(
            conn,
            "WHERE a.enabled AND a.id IN (SELECT automation_id FROM automation_trigger "
            "WHERE kind = 'linear' AND match_key = :team)",
            {"team": team},
        )
    results: list[dict[str, Any]] = []
    for record in records:
        schedule_id = record["id"]
        match = next(
            (
                (trigger.get("config") or {}, event)
                for trigger in record.get("triggers") or []
                if trigger.get("kind") == "linear"
                for event in sorted(events)
                if _linear_trigger_fires(
                    trigger.get("config") or {},
                    team,
                    event,
                    labels=labels,
                    added_labels=added_labels,
                    project=project,
                )
            ),
            None,
        )
        if match is None:
            continue
        config, event = match
        claim_prefix = f"{schedule_id}:linear:{team}:"
        claim_key = f"{claim_prefix}{delivery_id}"
        if not await _claim_delivery(
            claim_key, claim_prefix, config.get("max_runs_per_hour"), schedule_id
        ):
            continue
        try:
            result = await _launch_agent_schedule_record(
                record,
                repo=None,
                prompt=await _linear_event_prompt(
                    record, data, event, team=team, labels=labels, project=project
                ),
            )
        except Exception:
            logger.exception(
                "Failed to launch Linear automation", extra={"schedule_id": schedule_id}
            )
            await event_claims.release(_DELIVERY_CLAIM_SCOPE, claim_key)
            continue
        if result.get("status") != "started":
            logger.error(
                "Linear automation did not start",
                extra={"schedule_id": schedule_id, "launch_status": result.get("status")},
            )
            await event_claims.release(_DELIVERY_CLAIM_SCOPE, claim_key)
        results.append(result)
    return results


async def launch_scheduled_agent_run(
    schedule_id: str, trigger_id: str | None = None
) -> dict[str, Any]:
    """Run the automation a cron fired for; ``trigger_id`` names its schedule trigger.

    Crons made before triggers had ids send none and count as the first schedule.
    """
    record = await get_agent_schedule(schedule_id)
    if not record:
        if await get_value(SCHEDULES_NAMESPACE, schedule_id) is not None:
            # Not imported from the Store yet; the import keeps this cron.
            logger.warning(
                "Scheduled automation is awaiting its Store import",
                extra={"schedule_id": schedule_id},
            )
            return {"status": "pending_import", "schedule_id": schedule_id}
        await _delete_orphan_crons(schedule_id, keep=set())
        return {"status": "missing", "schedule_id": schedule_id}
    triggers = record.get("triggers") or []
    cron_ids = {t["cron_id"] for t in triggers if t.get("cron_id")}
    if trigger_id is None:
        # A cron from before trigger ids; one whose trigger was since replaced
        # would otherwise keep firing next to the new one.
        await _delete_orphan_crons(schedule_id, keep=cron_ids)
    schedules = [t for t in triggers if t.get("kind") == "schedule"]
    fired = next((t for t in schedules if t.get("id") == trigger_id), None) or (
        schedules[0] if schedules and trigger_id is None else None
    )
    if fired is None:
        await _delete_orphan_crons(schedule_id, keep=cron_ids)
        return {"status": "trigger_mismatch", "schedule_id": schedule_id}
    return await _launch_agent_schedule_record(record, repo=None)


async def trigger_agent_schedule(schedule_id: str) -> dict[str, Any]:
    record = await get_agent_schedule(schedule_id)
    _assert_schedule_exists(record)
    assert record is not None

    result = await _launch_agent_schedule_record(record, repo=_test_run_repo(record), test_run=True)
    status = result.get("status")
    if status == "started":
        return result
    if status == "unauthorized":
        status_code = result.get("status_code")
        raise HTTPException(
            status_code if isinstance(status_code, int) else 403,
            result.get("error") or "automation repository unavailable",
        )
    if status == "unknown_workspace":
        raise HTTPException(409, result.get("error") or "automation workspace no longer exists")
    raise HTTPException(502, result.get("error") or "failed to start automation test")
