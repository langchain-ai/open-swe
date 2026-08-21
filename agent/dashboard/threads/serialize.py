"""Thread metadata → the JSON shape the Agents UI renders.

Every reader of a thread's metadata lives here, so the UI's vocabulary (status,
category, origin, pull requests) is derived in one place instead of being
re-guessed by each endpoint that returns a thread.
"""

from collections.abc import Mapping
from typing import Any

from ...store import now_ms
from ...utils.github_refs import parse_github_pr_url
from ...utils.json_types import JsonObject, ThreadLike, as_json_object, thread_metadata
from ...utils.langsmith import get_langsmith_trace_url
from ..authz import thread_owner_login, thread_source, user_owns_thread
from ..options import SUPPORTED_MODEL_IDS, canonical_model_pair

# PR lifecycle states surfaced to the UI for a thread's associated pull request.
_PR_STATES: frozenset[str] = frozenset({"draft", "open", "merged", "closed"})
# Written while a sandbox is being created, so the id is not yet connectable.
SANDBOX_CREATING_SENTINEL = "__creating__"


def thread_id_of(thread: ThreadLike) -> str | None:
    thread_id = thread.get("thread_id") or thread.get("id")
    return thread_id if isinstance(thread_id, str) and thread_id else None


def thread_is_busy(thread: ThreadLike) -> bool:
    return thread.get("status") == "busy"


def metadata_string(metadata: Mapping[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def metadata_repo(metadata: Mapping[str, Any]) -> tuple[str, str, str]:
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


def repo_config_from_metadata(metadata: Mapping[str, Any]) -> dict[str, str]:
    owner, name, _ = metadata_repo(metadata)
    return {"owner": owner, "name": name} if owner and name else {}


def metadata_model_id(metadata: Mapping[str, Any]) -> str | None:
    for key in ("resolved_model", "model"):
        model = metadata.get(key)
        if isinstance(model, str) and model in SUPPORTED_MODEL_IDS:
            return model
        canonical = canonical_model_pair(model)
        if canonical is not None:
            return canonical[0]
    return None


def run_status_to_agent_status(thread_status: str | None, run_status: str | None) -> str:
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


def thread_run_id(metadata: Mapping[str, Any], latest_run_id: str | None) -> str | None:
    if latest_run_id:
        return latest_run_id
    run_id = metadata.get("latest_run_id")
    return run_id if isinstance(run_id, str) and run_id else None


def is_thread_viewed(metadata: Mapping[str, Any], latest_run_id: str | None) -> bool:
    viewed_at = metadata.get("last_viewed_at_ms")
    viewed_run_id = metadata.get("last_viewed_run_id")
    run_id = thread_run_id(metadata, latest_run_id)
    if run_id:
        return viewed_run_id == run_id
    return isinstance(viewed_at, (int, float))


def is_thread_resolved(metadata: Mapping[str, Any]) -> bool:
    return metadata.get("resolved") is True


def is_automation_thread(metadata: Mapping[str, Any]) -> bool:
    return (
        metadata_string(metadata, "thread_category") == "automation"
        or thread_source(metadata) == "schedule"
        or metadata_string(metadata, "schedule_id") is not None
    )


def thread_classification(metadata: Mapping[str, Any]) -> tuple[str, str, str]:
    source = thread_source(metadata)
    origin = metadata_string(metadata, "origin") or source
    trigger_kind = metadata_string(metadata, "trigger_kind") or (
        "schedule_test"
        if metadata.get("schedule_test") is True
        else "schedule"
        if source == "schedule" or metadata_string(metadata, "schedule_id")
        else "user"
    )
    category = metadata_string(metadata, "thread_category")
    if not category:
        source_context = metadata.get("source_context")
        if is_automation_thread(metadata):
            category = "automation"
        elif isinstance(metadata.get("pr_number"), int) or (
            isinstance(source_context, dict) and source_context.get("pr_number")
        ):
            category = "pull_request"
        elif isinstance(source_context, dict) and (
            source_context.get("github_issue") or source_context.get("linear_issue")
        ):
            category = "issue"
        else:
            category = "interactive"
    return category, origin, trigger_kind


def slack_thread_context(metadata: Mapping[str, Any]) -> JsonObject | None:
    """The Slack thread a run came from, as recorded in its source context."""
    source_context = metadata.get("source_context")
    if not isinstance(source_context, dict):
        return None
    slack_thread = source_context.get("slack_thread")
    return slack_thread if isinstance(slack_thread, dict) else None


def slack_thread_ids(slack_thread: Mapping[str, Any] | None) -> tuple[str, str] | None:
    """``(channel_id, thread_ts)`` — the pair every Slack write needs, or ``None``."""
    if slack_thread is None:
        return None
    channel_id = slack_thread.get("channel_id")
    thread_ts = slack_thread.get("thread_ts")
    if not isinstance(channel_id, str) or not channel_id.strip():
        return None
    if not isinstance(thread_ts, str) or not thread_ts.strip():
        return None
    return channel_id.strip(), thread_ts.strip()


def _thread_source_url(metadata: Mapping[str, Any]) -> str | None:
    if metadata.get("repo_private") is not True:
        return None
    slack_thread = slack_thread_context(metadata)
    permalink = slack_thread.get("permalink") if slack_thread else None
    return permalink.strip() if isinstance(permalink, str) and permalink.strip() else None


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


def _pull_requests(metadata: Mapping[str, Any], full_name: str, title: str) -> list[dict[str, Any]]:
    raw_pull_requests = metadata.get("pull_requests")
    records = raw_pull_requests if isinstance(raw_pull_requests, list) else []
    pull_requests = [
        parsed for record in records if (parsed := _pull_request_summary(record, title)) is not None
    ]
    if pull_requests:
        return pull_requests
    pr_number = metadata.get("pr_number")
    pr_url = metadata.get("pr_url")
    if not isinstance(pr_number, int) or not isinstance(pr_url, str):
        return []
    pr_ref = parse_github_pr_url(pr_url)
    legacy_repo = (
        full_name
        if full_name.count("/") == 1
        else f"{pr_ref.owner}/{pr_ref.repo}"
        if pr_ref
        else "unknown/unknown"
    )
    legacy_pr = _pull_request_summary(
        {
            "repo_full_name": legacy_repo,
            "number": pr_number,
            "url": pr_url,
            "title": metadata.get("pr_title"),
            "state": metadata.get("pr_state"),
            "head_ref": metadata.get("branch_name"),
            "base_ref": metadata.get("base_branch"),
            "diff_stats": as_json_object(metadata.get("diff_stats")),
        },
        title,
    )
    return [legacy_pr] if legacy_pr else []


async def thread_summary(
    thread: ThreadLike,
    *,
    latest_run_status: str | None = None,
    latest_run_id: str | None = None,
    owner_login: str | None = None,
    owner_email: str | None = None,
) -> dict[str, Any]:
    metadata = thread_metadata(thread)
    _, name, full_name = metadata_repo(metadata)
    created_at = metadata.get("created_at_ms")
    updated_at = metadata.get("updated_at_ms")
    raw_title = metadata.get("title")
    title: str = raw_title if isinstance(raw_title, str) else "Untitled agent"
    model = metadata.get("model") if isinstance(metadata.get("model"), str) else "Default"
    effort = metadata.get("effort") if isinstance(metadata.get("effort"), str) else None
    thread_status = thread.get("status") if isinstance(thread.get("status"), str) else "idle"
    metadata_run_status = metadata.get("latest_run_status")
    run_status = latest_run_status or (
        metadata_run_status if isinstance(metadata_run_status, str) else None
    )
    thread_category, origin, trigger_kind = thread_classification(metadata)

    thread_id = thread.get("thread_id") or thread.get("id")
    trace_url = await get_langsmith_trace_url(thread_id) if isinstance(thread_id, str) else None

    raw_sandbox_id = metadata.get("sandbox_id")
    sandbox_id = (
        raw_sandbox_id
        if isinstance(raw_sandbox_id, str)
        and raw_sandbox_id
        and raw_sandbox_id != SANDBOX_CREATING_SENTINEL
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
        "source": thread_source(metadata),
        "origin": origin,
        "threadCategory": thread_category,
        "triggerKind": trigger_kind,
        "automationId": metadata_string(metadata, "schedule_id"),
        "automationName": metadata_string(metadata, "schedule_name"),
        "status": run_status_to_agent_status(thread_status, run_status),
        "viewed": is_thread_viewed(metadata, latest_run_id),
        "viewedAt": (
            int(metadata["last_viewed_at_ms"])
            if isinstance(metadata.get("last_viewed_at_ms"), (int, float))
            else None
        ),
        "resolved": is_thread_resolved(metadata),
        "resolvedAt": (
            int(metadata["resolved_at_ms"])
            if isinstance(metadata.get("resolved_at_ms"), (int, float))
            else None
        ),
        "createdAt": int(created_at) if isinstance(created_at, (int, float)) else now_ms(),
        "updatedAt": int(updated_at) if isinstance(updated_at, (int, float)) else now_ms(),
        "ownerLogin": thread_owner_login(metadata),
        "isOwner": (user_owns_thread(metadata, owner_login, owner_email) if owner_login else True),
        "traceUrl": trace_url,
        "sourceUrl": _thread_source_url(metadata),
        "sandboxId": sandbox_id,
        # The transcript hydrates client-side from the SDK (`GET …/state` →
        # `stream.messages`); the summary only carries metadata.
        "messages": [],
    }
    pull_requests = _pull_requests(metadata, full_name, title)
    if pull_requests:
        latest_pr = pull_requests[-1]
        summary["pullRequests"] = pull_requests
        summary["pr"] = {
            key: latest_pr[key] for key in ("number", "title", "state", "headRef", "baseRef", "url")
        }
        summary["diffStats"] = latest_pr["diffStats"]
    return summary
