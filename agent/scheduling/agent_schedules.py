"""Recurring agent schedules: the stored record, and launching a run from one.

The dashboard owns the HTTP surface (:mod:`agent.dashboard.schedules`); this
module owns what a schedule *is* and what firing one does, so the scheduler
graph can launch a tick without importing the dashboard's request layer.
"""

import logging
import uuid
from typing import Any, Literal, TypedDict

from fastapi import HTTPException

from ..config import agent_version_metadata, langgraph_client
from ..dashboard.options import (
    SUPPORTED_MODEL_IDS,
    canonical_model_pair,
    gate_fable_model,
    model_supports_effort,
)
from ..dashboard.repo_access import require_repo_access_for_user
from ..dashboard.team_settings import get_team_fable_enabled
from ..dashboard.user_mappings import slack_id_for_login
from ..dispatch import create_durable_run
from ..input_messages import InputMessageContext, build_run_input
from ..store import get_value, now_iso, now_ms, put_value
from ..utils.slack import (
    bind_slack_thread_id,
    post_slack_top_level_message_with_ts,
    store_slack_run_mapping,
)
from ..utils.thread_participants import PARTICIPANT_LOGINS_KEY

logger = logging.getLogger(__name__)

SCHEDULES_NAMESPACE: list[str] = ["agent_schedules"]
SCHEDULE_RUN_STATE_NAMESPACE: list[str] = ["agent_schedule_run_state"]
SCHEDULE_TASK = "schedule"
AGENT_ASSISTANT_ID = "agent"

SlackNotificationMode = Literal["always", "on_action"]
DEFAULT_SLACK_NOTIFICATION_MODE: SlackNotificationMode = "always"


class SchedulePayload(TypedDict):
    schedule_id: str


def slack_notification_mode(record: dict[str, Any]) -> SlackNotificationMode:
    return "on_action" if record.get("slack_notification_mode") == "on_action" else "always"


def repo_full_name(repo: dict[str, str] | None) -> str | None:
    if not repo:
        return None
    owner = repo.get("owner")
    name = repo.get("name")
    return f"{owner}/{name}" if owner and name else None


def normalize_model_choice(
    model_id: str | None, effort: str | None
) -> tuple[str | None, str | None]:
    if not isinstance(model_id, str):
        return None, None
    if model_id not in SUPPORTED_MODEL_IDS:
        canonical = canonical_model_pair(model_id, effort)
        return canonical if canonical is not None else (None, None)
    if not isinstance(effort, str) or not model_supports_effort(model_id, effort):
        return None, None
    return model_id, effort


async def get_agent_schedule(schedule_id: str) -> dict[str, Any] | None:
    return await get_value(SCHEDULES_NAMESPACE, schedule_id)


async def put_agent_schedule(record: dict[str, Any]) -> dict[str, Any]:
    record = {**record, "updated_at": now_iso()}
    await put_value(SCHEDULES_NAMESPACE, record["id"], record)
    return record


async def get_run_state(schedule_id: str) -> dict[str, Any] | None:
    return await get_value(SCHEDULE_RUN_STATE_NAMESPACE, schedule_id)


async def _put_run_state(record: dict[str, Any], patch: dict[str, Any]) -> None:
    schedule_id = record["id"]
    existing = await get_run_state(schedule_id)
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
        "created_by": record.get("created_by"),
        "user_email": record.get("user_email"),
    }
    await put_value(SCHEDULE_RUN_STATE_NAMESPACE, schedule_id, value)


def _slack_root_message(record: dict[str, Any], *, test_run: bool = False) -> str:
    repo = repo_full_name(record.get("repo") if isinstance(record.get("repo"), dict) else None)
    repo_line = f"\n*Repository:* `{repo}`" if repo else ""
    run_kind = "test" if test_run else "scheduled"
    return (
        f"*Open SWE automation:* {record.get('name') or 'Scheduled agent'}{repo_line}\n\n"
        f"A {run_kind} run started. Reply in this thread to follow up with the agent."
    )


def _conditional_slack_channel(record: dict[str, Any]) -> str | None:
    channel_id = record.get("slack_channel_id")
    if slack_notification_mode(record) != "on_action":
        return None
    return channel_id if isinstance(channel_id, str) and channel_id else None


def _scheduled_prompt(record: dict[str, Any], slack_thread: dict[str, Any] | None) -> str:
    prompt = str(record["prompt"])
    if slack_thread:
        return (
            f"{prompt}\n\n"
            "Use `slack_thread_reply` for clarifying questions, essential progress updates, "
            "the pull request link, and the final outcome in the connected Slack thread."
        )
    if _conditional_slack_channel(record):
        return (
            f"{prompt}\n\n"
            "This automation uses conditional Slack notifications. If and only if you perform "
            "a concrete requested action, such as changing code or updating an external system, "
            "call `notify_automation_channel` exactly once with a concise final outcome. Do not "
            "call it for read-only checks or when no action was needed."
        )
    return prompt


def _agent_run_metadata(
    record: dict[str, Any],
    slack_thread: dict[str, Any] | None = None,
    *,
    test_run: bool = False,
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
        "schedule_name": record.get("name"),
        "schedule_test": test_run,
        "github_login": record.get("created_by"),
        PARTICIPANT_LOGINS_KEY: [record["created_by"]] if record.get("created_by") else [],
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
        metadata["source_context"] = {"slack_thread": slack_thread}
    return metadata


async def _agent_run_config(
    record: dict[str, Any],
    thread_id: str,
    slack_thread: dict[str, Any] | None = None,
    *,
    test_run: bool = False,
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
    conditional_channel = _conditional_slack_channel(record)
    if conditional_channel:
        configurable["automation_slack_notification"] = {
            "channel_id": conditional_channel,
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


class SlackRootMessageFailed(Exception):
    """Slack refused the automation's root message, so the run has nowhere to report."""


async def _fail_run(record: dict[str, Any], error: str, **extra: Any) -> dict[str, Any]:
    await _put_run_state(record, {"last_error": error, "last_error_at": now_iso()})
    return {"schedule_id": record["id"], "error": error, **extra}


async def _open_slack_thread(
    record: dict[str, Any], thread_id: str, *, test_run: bool
) -> dict[str, Any] | None:
    """The Slack root message this run reports into, or ``None`` when unused."""
    channel_id = record.get("slack_channel_id")
    if slack_notification_mode(record) != "always" or not (
        isinstance(channel_id, str) and channel_id
    ):
        return None
    message_ts, slack_error = await post_slack_top_level_message_with_ts(
        channel_id,
        _slack_root_message(record, test_run=test_run),
        unfurl_links=False,
        unfurl_media=False,
    )
    if not message_ts:
        raise SlackRootMessageFailed(slack_error or "unknown error")
    created_by = record.get("created_by")
    slack_thread = {
        "channel_id": channel_id,
        "thread_ts": message_ts,
        "triggering_event_ts": message_ts,
        "triggering_user_id": await slack_id_for_login(
            created_by if isinstance(created_by, str) else None
        )
        or "",
        "triggering_user_email": record.get("user_email") or "",
    }
    await bind_slack_thread_id(langgraph_client(), channel_id, message_ts, thread_id)
    return slack_thread


async def launch_agent_schedule_record(
    record: dict[str, Any], *, test_run: bool = False
) -> dict[str, Any]:
    """Start one fresh agent thread for ``record`` and remember the outcome."""
    schedule_id = record["id"]
    if not test_run and not record.get("enabled"):
        return {"status": "disabled", "schedule_id": schedule_id}

    full_name = repo_full_name(record.get("repo") if isinstance(record.get("repo"), dict) else None)
    login = record.get("created_by")
    if full_name:
        if not (isinstance(login, str) and login):
            return await _fail_run(record, "schedule owner unavailable", status="unauthorized")
        try:
            await require_repo_access_for_user(login, full_name)
        except HTTPException as exc:
            return await _fail_run(
                record,
                exc.detail,
                status="unauthorized",
                status_code=exc.status_code,
            )

    thread_id = str(uuid.uuid4())
    try:
        slack_thread = await _open_slack_thread(record, thread_id, test_run=test_run)
    except SlackRootMessageFailed as exc:
        return await _fail_run(record, f"Slack post failed: {exc}", status="error")

    client = langgraph_client()
    metadata = _agent_run_metadata(record, slack_thread, test_run=test_run)
    await client.threads.create(thread_id=thread_id, metadata=metadata, if_exists="do_nothing")
    await client.threads.update(thread_id=thread_id, metadata=metadata)

    slack_channel_id = record.get("slack_channel_id")
    has_channel = isinstance(slack_channel_id, str) and bool(slack_channel_id)
    input_context: InputMessageContext = {
        "sender_id": f"system:schedule:{schedule_id}",
        "surface": "automation",
        "kind": "system",
    }
    if has_channel:
        input_context["channel_id"] = f"slack:{slack_channel_id}"
    run = await create_durable_run(
        thread_id,
        AGENT_ASSISTANT_ID,
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
                [{"id": f"slack:{slack_channel_id}", "platform": "slack"}] if has_channel else None
            ),
        ),
        source="schedule",
        config=await _agent_run_config(record, thread_id, slack_thread, test_run=test_run),
        client=client,
        stream_mode=["values", "updates", "messages-tuple"],
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
    return await launch_agent_schedule_record(record)
