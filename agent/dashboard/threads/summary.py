"""Thread metadata readers and the summary shape the dashboard renders."""

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlencode

from fastapi import HTTPException

from agent.dashboard.admin import is_admin
from agent.dashboard.options import SUPPORTED_MODEL_IDS, canonical_model_pair
from agent.slack.client import parse_github_pr_url
from agent.slack.code_channels import CODE_CHANNEL_SESSION_TS
from agent.slack.oauth import SLACK_TEAM_ID
from agent.source_context import SourceContext
from agent.utils.json_types import (
    JsonObject,
    ThreadLike,
    as_json_object,
    as_thread_dict,
    thread_metadata,
)
from agent.utils.langsmith import get_langsmith_trace_url
from agent.utils.timing import phase

logger = logging.getLogger(__name__)

_DASHBOARD_SOURCE = "dashboard"
# Sources whose threads should surface in the Agents UI (besides "dashboard").
_SURFACED_SOURCES: tuple[str, ...] = ("dashboard", "github", "slack", "linear", "schedule")
# PR lifecycle states surfaced to the UI for a thread's associated pull request.
_PR_STATES: frozenset[str] = frozenset({"draft", "open", "merged", "closed"})
_SANDBOX_CREATING_SENTINEL = "__creating__"

_ThreadSortBy = Literal["created_at", "updated_at"]


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _parse_repo(full_name: str | None) -> dict[str, str] | None:
    if not isinstance(full_name, str):
        return None
    parts = full_name.strip().split("/", 1)
    if len(parts) != 2:
        return None
    owner, name = parts[0].strip(), parts[1].strip()
    if not owner or not name:
        return None
    return {"owner": owner, "name": name}


def _thread_is_busy(thread: ThreadLike) -> bool:
    return thread.get("status") == "busy"


def _thread_id(thread: ThreadLike) -> str | None:
    thread_id = thread.get("thread_id") or thread.get("id")
    return thread_id if isinstance(thread_id, str) and thread_id else None


def _thread_metadata(thread: ThreadLike) -> JsonObject:
    return thread_metadata(thread)


def _thread_source(metadata: Mapping[str, Any]) -> str:
    source = metadata.get("source")
    return source if isinstance(source, str) and source else _DASHBOARD_SOURCE


def _metadata_model_id(metadata: Mapping[str, Any]) -> str | None:
    for key in ("resolved_model", "model"):
        model = metadata.get(key)
        if isinstance(model, str) and model in SUPPORTED_MODEL_IDS:
            return model
        canonical = canonical_model_pair(model)
        if canonical is not None:
            return canonical[0]
    return None


def _thread_is_readable(metadata: Mapping[str, Any]) -> bool:
    """Any surfaced-source thread is readable by authenticated users.

    Dashboard login is already gated by ``ALLOWED_GITHUB_ORGS`` (see
    ``oauth.enforce_org_login_gate``), so any logged-in user is a trusted
    org member. This lets teammates open "Open in Web" links shared in Slack
    threads with read-only access.
    """
    return _thread_source(metadata) in _SURFACED_SOURCES


def _assert_thread_readable(metadata: Mapping[str, Any]) -> None:
    if not _thread_is_readable(metadata):
        raise HTTPException(404, "thread not found")


def _assert_thread_postable(
    metadata: Mapping[str, Any], login: str, email: str | None = None
) -> None:
    _assert_thread_readable(metadata)
    if (metadata.get("admin_thread") is True or _is_automation_thread(metadata)) and not is_admin(
        email, login=login
    ):
        raise HTTPException(403, "only admins can send messages in this thread")


def _metadata_repo(metadata: Mapping[str, Any]) -> tuple[str, str, str]:
    owner = metadata.get("repo_owner")
    name = metadata.get("repo_name")
    if isinstance(owner, str) and isinstance(name, str) and owner and name:
        return owner, name, f"{owner}/{name}"
    repo = metadata.get("repo")
    if isinstance(repo, dict):
        o = repo.get("owner")
        n = repo.get("name")
        if isinstance(o, str) and isinstance(n, str) and o and n:
            return o, n, f"{o}/{n}"
    return "", "", ""


def _repo_config_from_metadata(metadata: Mapping[str, Any]) -> dict[str, str]:
    owner, name, _ = _metadata_repo(metadata)
    if owner and name:
        return {"owner": owner, "name": name}
    return {}


def _run_status_to_agent_status(thread_status: str | None, run_status: str | None) -> str:
    # "interrupted" wins over a still-``busy`` thread: cancellation is async, so a
    # just-cancelled thread reports busy for a moment and would otherwise look
    # like it is still running. Callers refresh the newest run's real status
    # first, so a follow-up run that superseded an interrupted one reads as
    # pending/running here.
    if run_status == "interrupted":
        return "interrupted"
    if thread_status == "busy" or run_status in {"pending", "running"}:
        return "running"
    if run_status in {"error", "failed", "timeout"}:
        return "error"
    if run_status == "success":
        return "finished"
    return "idle"


def _thread_run_id(metadata: Mapping[str, Any], latest_run_id: str | None) -> str | None:
    if latest_run_id:
        return latest_run_id
    run_id = metadata.get("latest_run_id")
    return run_id if isinstance(run_id, str) and run_id else None


def _is_thread_viewed(metadata: Mapping[str, Any], latest_run_id: str | None) -> bool:
    viewed_at = metadata.get("last_viewed_at_ms")
    viewed_run_id = metadata.get("last_viewed_run_id")
    run_id = _thread_run_id(metadata, latest_run_id)
    if run_id:
        return viewed_run_id == run_id
    return isinstance(viewed_at, (int, float))


def _is_thread_resolved(metadata: Mapping[str, Any]) -> bool:
    return metadata.get("resolved") is True


def _thread_source_url(metadata: Mapping[str, Any]) -> str | None:
    slack_thread = SourceContext.from_metadata(metadata).slack_thread
    if slack_thread is None:
        return None
    return slack_thread.permalink.strip() or None


def _thread_source_app_url(metadata: Mapping[str, Any]) -> str | None:
    slack_thread = SourceContext.from_metadata(metadata).slack_thread
    team_id = SLACK_TEAM_ID.strip()
    if (
        slack_thread is None
        or not team_id
        or not slack_thread.channel_id
        or not slack_thread.thread_ts
    ):
        return None
    return f"slack://channel?{urlencode({'team': team_id, 'id': slack_thread.channel_id, 'message': slack_thread.thread_ts})}"


def _code_channel_url(metadata: Mapping[str, Any]) -> str | None:
    slack_thread = SourceContext.from_metadata(metadata).slack_thread
    if slack_thread is None or slack_thread.thread_ts != CODE_CHANNEL_SESSION_TS:
        return None
    channel_id = slack_thread.channel_id.strip()
    team_id = SLACK_TEAM_ID.strip()
    if not channel_id or not team_id:
        return None
    return f"https://slack.com/app_redirect?{urlencode({'channel': channel_id, 'team': team_id})}"


def _metadata_string(metadata: Mapping[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_automation_thread(metadata: Mapping[str, Any]) -> bool:
    return (
        _metadata_string(metadata, "thread_category") == "automation"
        or _thread_source(metadata) == "schedule"
        or _metadata_string(metadata, "schedule_id") is not None
    )


def _thread_classification(metadata: Mapping[str, Any]) -> tuple[str, str, str]:
    source = _thread_source(metadata)
    origin = _metadata_string(metadata, "origin") or source
    trigger_kind = _metadata_string(metadata, "trigger_kind") or (
        "schedule_test"
        if metadata.get("schedule_test") is True
        else "schedule"
        if source == "schedule" or _metadata_string(metadata, "schedule_id")
        else "user"
    )
    category = _metadata_string(metadata, "thread_category")
    if not category:
        context = SourceContext.from_metadata(metadata)
        if _is_automation_thread(metadata):
            category = "automation"
        elif isinstance(metadata.get("pr_number"), int) or context.pr_number:
            category = "pull_request"
        elif context.github_issue or context.linear_issue:
            category = "issue"
        else:
            category = "interactive"
    return category, origin, trigger_kind


def _thread_timestamp_ms(thread: ThreadLike, field: _ThreadSortBy) -> int:
    metadata = _thread_metadata(thread)
    value = metadata.get(f"{field}_ms")
    if isinstance(value, (int, float)):
        return int(value)
    timestamp = thread.get(field)
    if isinstance(timestamp, str) and timestamp:
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            return 0
        return int(parsed.timestamp() * 1000)
    return 0


def _thread_updated_ms(thread: ThreadLike) -> int:
    return _thread_timestamp_ms(thread, "updated_at")


def _pull_request_summary(record: object, fallback_title: str) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    repo_full_name = record.get("repo_full_name")
    number = record.get("number")
    url = record.get("url")
    if (
        not isinstance(repo_full_name, str)
        or repo_full_name.count("/") != 1
        or not isinstance(number, int)
        or isinstance(number, bool)
        or not isinstance(url, str)
    ):
        return None
    title = record.get("title")
    state = record.get("state")
    stats = record.get("diff_stats")
    stats = stats if isinstance(stats, dict) else {}
    return {
        "repoFullName": repo_full_name,
        "number": number,
        "title": title if isinstance(title, str) and title else fallback_title,
        "state": state if state in _PR_STATES else "open",
        "headRef": record.get("head_ref") if isinstance(record.get("head_ref"), str) else "",
        "baseRef": record.get("base_ref") if isinstance(record.get("base_ref"), str) else "main",
        "url": url,
        "author": record.get("author") if isinstance(record.get("author"), str) else None,
        "authorAvatarUrl": (
            record.get("author_avatar_url")
            if isinstance(record.get("author_avatar_url"), str)
            else None
        ),
        "createdAt": record.get("created_at")
        if isinstance(record.get("created_at"), str)
        else None,
        "diffStats": {
            key: max(0, value) if isinstance(value := stats.get(key), int) else 0
            for key in ("files", "additions", "deletions")
        },
    }


async def _thread_summary(
    thread: ThreadLike,
    *,
    latest_run_status: str | None = None,
    latest_run_id: str | None = None,
) -> dict[str, Any]:
    metadata = thread_metadata(thread)
    owner, name, full_name = _metadata_repo(metadata)
    created_at = metadata.get("created_at_ms")
    if not isinstance(created_at, (int, float)):
        created_at = _thread_timestamp_ms(thread, "created_at")
    updated_at = metadata.get("updated_at_ms")
    if not isinstance(updated_at, (int, float)):
        updated_at = _thread_timestamp_ms(thread, "updated_at")
    raw_title = metadata.get("title")
    title: str = raw_title if isinstance(raw_title, str) else "Untitled agent"
    model = metadata.get("model") if isinstance(metadata.get("model"), str) else "Default"
    effort = metadata.get("effort") if isinstance(metadata.get("effort"), str) else None
    thread_status = thread.get("status") if isinstance(thread.get("status"), str) else "idle"
    metadata_run_status = metadata.get("latest_run_status")
    run_status = latest_run_status or (
        metadata_run_status if isinstance(metadata_run_status, str) else None
    )
    status = _run_status_to_agent_status(thread_status, run_status)

    pr_number = metadata.get("pr_number")
    pr_url = metadata.get("pr_url")
    pr_title = metadata.get("pr_title")
    pr_state = metadata.get("pr_state")
    thread_category, origin, trigger_kind = _thread_classification(metadata)

    thread_id = thread.get("thread_id") or thread.get("id")
    trace_url = await get_langsmith_trace_url(thread_id) if isinstance(thread_id, str) else None

    raw_sandbox_id = metadata.get("sandbox_id")
    sandbox_id = (
        raw_sandbox_id
        if isinstance(raw_sandbox_id, str)
        and raw_sandbox_id
        and raw_sandbox_id != _SANDBOX_CREATING_SENTINEL
        else None
    )

    summary: dict[str, Any] = {
        "id": thread_id,
        "title": title,
        "repo": name,
        "repoFullName": full_name,
        "branch": metadata.get("branch_name") or metadata.get("base_branch") or "main",
        "model": model,
        "effort": effort,
        "planMode": metadata.get("plan_mode") is True,
        "adminThread": metadata.get("admin_thread") is True,
        "environment": metadata.get("environment"),
        "planStatus": metadata.get("plan_status"),
        "source": _thread_source(metadata),
        "origin": origin,
        "threadCategory": thread_category,
        "triggerKind": trigger_kind,
        "automationId": _metadata_string(metadata, "schedule_id"),
        "automationName": _metadata_string(metadata, "schedule_name"),
        "automationActionPosted": (
            thread_category == "automation"
            and _metadata_string(metadata, "automation_action_posted_at") is not None
        ),
        "status": status,
        "viewed": _is_thread_viewed(metadata, latest_run_id),
        "viewedAt": (
            int(metadata["last_viewed_at_ms"])
            if isinstance(metadata.get("last_viewed_at_ms"), (int, float))
            else None
        ),
        "resolved": _is_thread_resolved(metadata),
        "attentionReason": _metadata_string(metadata, "attention_reason"),
        "resolvedAt": (
            int(metadata["resolved_at_ms"])
            if isinstance(metadata.get("resolved_at_ms"), (int, float))
            else None
        ),
        "createdAt": int(created_at) if isinstance(created_at, (int, float)) else _now_ms(),
        "updatedAt": int(updated_at) if isinstance(updated_at, (int, float)) else _now_ms(),
        "traceUrl": trace_url,
        "sourceUrl": _thread_source_url(metadata),
        "sourceAppUrl": _thread_source_app_url(metadata),
        "codeChannelUrl": _code_channel_url(metadata),
        "sandboxId": sandbox_id,
    }
    raw_pull_requests = metadata.get("pull_requests")
    pull_request_records = raw_pull_requests if isinstance(raw_pull_requests, list) else []
    pull_requests = [
        parsed
        for record in pull_request_records
        if (parsed := _pull_request_summary(record, title)) is not None
    ]
    if not pull_requests and isinstance(pr_number, int) and isinstance(pr_url, str):
        pr_ref = parse_github_pr_url(pr_url)
        legacy_repo = (
            full_name
            if full_name.count("/") == 1
            else f"{pr_ref.owner}/{pr_ref.repo}"
            if pr_ref
            else "unknown/unknown"
        )
        legacy_record = {
            "repo_full_name": legacy_repo,
            "number": pr_number,
            "url": pr_url,
            "title": pr_title,
            "state": pr_state,
            "head_ref": metadata.get("branch_name"),
            "base_ref": metadata.get("base_branch"),
            "diff_stats": as_json_object(metadata.get("diff_stats")),
        }
        legacy_pr = _pull_request_summary(legacy_record, title)
        if legacy_pr:
            pull_requests.append(legacy_pr)
    if pull_requests:
        latest_pr = pull_requests[-1]
        summary["pullRequests"] = pull_requests
        summary["pr"] = {
            key: latest_pr[key] for key in ("number", "title", "state", "headRef", "baseRef", "url")
        }
        summary["diffStats"] = latest_pr["diffStats"]
    # The transcript hydrates client-side from the SDK (`GET …/state` →
    # `stream.messages`); the summary only carries metadata.
    summary["messages"] = []
    return summary


async def _latest_run_info(client: Any, thread_id: str) -> tuple[str | None, str | None]:
    try:
        runs = await client.runs.list(thread_id, limit=1)
    except Exception:  # noqa: BLE001
        logger.debug("Could not fetch latest run for thread %s", thread_id, exc_info=True)
        return None, None
    if not runs:
        return None, None
    run = runs[0]
    raw_status = run.get("status") if isinstance(run, dict) else getattr(run, "status", None)
    raw_id = (
        (run.get("run_id") or run.get("id"))
        if isinstance(run, dict)
        else (getattr(run, "run_id", None) or getattr(run, "id", None))
    )
    status = raw_status.lower() if isinstance(raw_status, str) else None
    run_id = raw_id if isinstance(raw_id, str) and raw_id else None
    return status, run_id


async def _refresh_latest_run_metadata(
    client: Any, thread: ThreadLike, *, timings: dict[str, float] | None = None
) -> tuple[ThreadLike, str | None, str | None]:
    record = timings if timings is not None else {}
    thread_id = thread.get("thread_id") or thread.get("id")
    if not isinstance(thread_id, str) or not thread_id:
        return thread, None, None
    with phase(record, "runs_list"):
        latest_run_status, latest_run_id = await _latest_run_info(client, thread_id)
    metadata = thread_metadata(thread)
    metadata_update: dict[str, Any] = {}
    if latest_run_status and latest_run_status != metadata.get("latest_run_status"):
        metadata_update["latest_run_status"] = latest_run_status
    if latest_run_id and latest_run_id != metadata.get("latest_run_id"):
        metadata_update["latest_run_id"] = latest_run_id
    if metadata_update:
        with phase(record, "thread_update"):
            try:
                await client.threads.update(thread_id=thread_id, metadata=metadata_update)
            except Exception:  # noqa: BLE001
                logger.debug(
                    "Could not persist latest run metadata for %s", thread_id, exc_info=True
                )
            else:
                thread = {**as_thread_dict(thread), "metadata": {**metadata, **metadata_update}}
    return thread, latest_run_status, latest_run_id
