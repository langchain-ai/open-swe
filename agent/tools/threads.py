"""Discover, inspect, and manage Open SWE threads."""

import asyncio
import json
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import HTTPException
from langchain_core.messages import BaseMessage
from langgraph.config import get_config
from langgraph.prebuilt import InjectedState

from agent.dashboard import plan_api, workflow_approval_api
from agent.dashboard.admin import is_admin
from agent.dashboard.agent_overrides import resolve_login_from_email_async
from agent.dashboard.oauth import enforce_org_login_gate
from agent.dashboard.options import SUPPORTED_MODEL_IDS, canonical_model_pair, model_supports_effort
from agent.dashboard.plan_store import get_plan_content, list_plan_comments
from agent.dashboard.thread_api import (
    ThreadMessageBody,
    admin_cancel_dashboard_thread,
    cancel_dashboard_thread,
    delete_dashboard_thread,
    get_dashboard_thread,
    list_dashboard_threads_page,
    proxy_dashboard_thread_commands,
    resolve_dashboard_thread,
    send_dashboard_message,
)
from agent.dashboard.workflow_approval import (
    WORKFLOW_APPROVAL_PENDING,
    get_workflow_push_approvals,
    workflow_push_approval_responses,
)
from agent.input_messages import input_message_text, message_sender_id
from agent.slack.client import parse_github_pr_url
from agent.utils.dashboard_links import (
    dashboard_plan_url,
    dashboard_thread_id,
    dashboard_thread_url,
)
from agent.utils.json_types import as_json_object, thread_metadata
from agent.utils.langsmith import (
    LangSmithCostUnavailable,
    get_langsmith_thread_cost,
    get_open_swe_thread_id_from_langsmith,
    parse_langsmith_locator,
)
from agent.utils.thread_ops import langgraph_client
from agent.utils.thread_participants import PARTICIPANT_LOGINS_KEY, participant_logins

logger = logging.getLogger(__name__)

ThreadScope = Literal["all", "interactive", "automation"]
ThreadAction = Literal[
    "send_message",
    "cancel",
    "admin_cancel",
    "resolve",
    "unresolve",
    "delete",
    "add_plan_comment",
    "delete_plan_comment",
    "update_plan",
    "approve_plan",
    "request_plan_changes",
    "approve_workflow_push",
    "reject_workflow_push",
]
PlanFormat = Literal["html", "markdown"]
_MAX_MESSAGE_CHARS = 20_000
_MAX_COMMENT_CHARS = 20_000
_MAX_DETAIL_MESSAGE_CHARS = 4_000
_MAX_TRANSCRIPT_MESSAGES = 100
_MAX_TRANSCRIPT_CHARS = 50_000
_MAX_RUNS = 25
_MAX_INSPECTION_CONTENT_CHARS = 50_000
_MAX_PLAN_COMMENTS = 100
_MAX_PLAN_CHARS = 500_000


@dataclass(frozen=True)
class _Actor:
    login: str
    email: str | None
    name: str

    @property
    def session(self) -> dict[str, Any]:
        return {"sub": self.login, "email": self.email, "name": self.name}

    @property
    def admin(self) -> bool:
        return is_admin(self.email, login=self.login)


def _config() -> dict[str, Any]:
    try:
        config = get_config()
    except Exception:
        return {}
    return as_json_object(config)


async def _actor(state: Mapping[str, Any] | None = None) -> _Actor | None:
    config = _config()
    configurable = as_json_object(config.get("configurable"))
    email_value = configurable.get("user_email")
    email = email_value.strip() if isinstance(email_value, str) and email_value.strip() else None
    login_value = configurable.get("github_login")
    login = login_value.strip() if isinstance(login_value, str) and login_value.strip() else None
    if not login:
        login = await resolve_login_from_email_async(email)
    if not login:
        return None
    current_login = _latest_state_github_login(state)
    if current_login and current_login.lower() != login.lower():
        login = current_login
        email = None
    try:
        await enforce_org_login_gate(login)
    except HTTPException:
        return None
    return _Actor(login=login, email=email, name=login)


def _failure(error: str, *, status_code: int | None = None) -> dict[str, Any]:
    response: dict[str, Any] = {"success": False, "error": error}
    if status_code is not None:
        response["status_code"] = status_code
    return response


def _http_failure(exc: HTTPException) -> dict[str, Any]:
    detail = exc.detail if isinstance(exc.detail, str) else "thread operation failed"
    return _failure(detail, status_code=exc.status_code)


def _web_link(item: Mapping[str, Any]) -> str | None:
    thread_id = item.get("id")
    return dashboard_thread_url(thread_id) if isinstance(thread_id, str) else None


def _langsmith_identifiers(trace_url: Any, locator: str | None = None) -> dict[str, Any]:
    trusted = parse_langsmith_locator(trace_url) if isinstance(trace_url, str) else None
    supplied = parse_langsmith_locator(locator) if locator else None
    supplied_run_id = locator.strip() if locator and _looks_uuid(locator) else None
    return {
        "trace_url": trace_url if trusted else None,
        "thread_id": trusted.id if trusted and trusted.kind == "thread" else None,
        "run_id": supplied.id if supplied and supplied.kind == "run" else supplied_run_id,
    }


def _list_item(item: Mapping[str, Any], *, locator: str | None = None) -> dict[str, Any]:
    result = dict(item)
    result.pop("messages", None)
    result.pop("sandboxId", None)
    result["webUrl"] = _web_link(item)
    langsmith = _langsmith_identifiers(item.get("traceUrl"), locator)
    if any(value is not None for value in langsmith.values()):
        result["langsmith"] = langsmith
    return result


async def list_threads(
    participant: str | None = None,
    all_users: bool = False,
    limit: int = 25,
    offset: int = 0,
    resolved: bool | None = None,
    viewed: bool | None = None,
    source: str | None = None,
    status: str | None = None,
    query: str | None = None,
    scope: ThreadScope = "all",
    automation_id: str | None = None,
    admin_threads: bool | None = None,
    state: Annotated[dict[str, Any] | None, InjectedState] = None,
) -> dict[str, Any]:
    """List Open SWE threads the current user, one other participant, or everyone joined."""
    actor = await _actor(state)
    if actor is None:
        return _failure("No verified triggering user is available")
    requested = (
        participant.strip() if isinstance(participant, str) and participant.strip() else None
    )
    if requested and all_users:
        return _failure("participant and all_users cannot be used together")
    if scope not in {"all", "interactive", "automation"}:
        return _failure("scope must be all, interactive, or automation")
    cross_user = bool(requested and requested.lower() != actor.login.lower())
    if admin_threads is not None and not actor.admin:
        return _failure("Only workspace admins can filter admin threads")
    if (all_users or cross_user) and not actor.admin:
        return _failure("Only workspace admins can list other users' threads")

    filter_participant_login = requested if cross_user else None
    try:
        page = await list_dashboard_threads_page(
            actor.login,
            email=actor.email,
            limit=limit,
            offset=offset,
            include_all=all_users or admin_threads is True,
            resolved=resolved,
            viewed=viewed,
            source=source,
            status=status,
            query=query,
            scope=scope,
            automation_id=automation_id,
            filter_participant_login=filter_participant_login,
            surfaced_only=True,
            admin_threads=admin_threads,
        )
    except HTTPException as exc:
        return _http_failure(exc)
    except Exception:
        logger.exception("Could not list threads")
        return _failure("Could not list threads")
    return {
        "success": True,
        "items": [_list_item(item) for item in page.get("items", [])],
        "limit": page.get("limit"),
        "offset": page.get("offset"),
        "has_more": page.get("hasMore", False),
    }


def _value(record: Any, key: str) -> Any:
    return record.get(key) if isinstance(record, Mapping) else getattr(record, key, None)


def _message_content(message: Any) -> Any:
    return _value(message, "content")


def _message_kind(message: Any) -> str:
    value = _value(message, "type") or _value(message, "role")
    return value.lower() if isinstance(value, str) else ""


def _message_timestamp(message: Any) -> str | None:
    created_at = _value(message, "created_at")
    if created_at is not None:
        return str(created_at)
    response_metadata = _value(message, "response_metadata")
    if isinstance(response_metadata, Mapping):
        metadata_created_at = response_metadata.get("created_at")
        if metadata_created_at is not None:
            return str(metadata_created_at)
    return None


def _plain_message_text(content: Any) -> str | None:
    structured = input_message_text(content)
    if structured:
        return structured
    values = content if isinstance(content, list) else [content]
    texts: list[str] = []
    for value in values:
        if isinstance(value, Mapping):
            block_type = value.get("type")
            if block_type not in {None, "text"}:
                continue
            text = value.get("text")
        else:
            text = value
        if not isinstance(text, str):
            continue
        stripped = text.strip()
        if not stripped or stripped.startswith(("<dynamic-context", "<system-instructions")):
            continue
        if stripped.startswith("<input-message"):
            continue
        texts.append(stripped)
    combined = "\n\n".join(texts).strip()
    return combined or None


def _state_messages(state: Any) -> list[Any]:
    values = _value(state, "values")
    messages = values.get("messages") if isinstance(values, Mapping) else None
    return messages if isinstance(messages, list) else []


def _last_user_message(state: Any) -> dict[str, Any] | None:
    for message in reversed(_state_messages(state)):
        kind = _message_kind(message)
        if not isinstance(message, (Mapping, BaseMessage)) or kind not in {"human", "user"}:
            continue
        content = _message_content(message)
        text = _plain_message_text(content)
        if not text:
            continue
        truncated = len(text) > _MAX_DETAIL_MESSAGE_CHARS
        return {
            "text": text[:_MAX_DETAIL_MESSAGE_CHARS],
            "truncated": truncated,
            "sender_id": message_sender_id(content),
            "timestamp": _message_timestamp(message),
        }
    return None


def _message_id(message: Any) -> str | None:
    value = _value(message, "id")
    return str(value) if value else None


def _transcript(state: Any) -> dict[str, Any]:
    messages = _state_messages(state)
    visible: list[dict[str, Any]] = []
    omitted = 0
    used_chars = 0
    for message in messages:
        kind = _message_kind(message)
        if kind not in {"human", "user", "ai", "assistant"}:
            omitted += 1
            continue
        content = _message_content(message)
        text = _plain_message_text(content)
        if not text:
            omitted += 1
            continue
        if len(visible) >= _MAX_TRANSCRIPT_MESSAGES or used_chars >= _MAX_TRANSCRIPT_CHARS:
            omitted += 1
            continue
        remaining = _MAX_TRANSCRIPT_CHARS - used_chars
        returned_text = text[: min(_MAX_DETAIL_MESSAGE_CHARS, remaining)]
        visible.append(
            {
                "id": _message_id(message),
                "role": "user" if kind in {"human", "user"} else "assistant",
                "text": returned_text,
                "truncated": len(returned_text) < len(text),
                "sender_id": message_sender_id(content),
                "timestamp": _message_timestamp(message),
            }
        )
        used_chars += len(returned_text)
    return {
        "messages": visible,
        "message_count": len(messages),
        "returned_count": len(visible),
        "omitted_count": omitted,
        "truncated": omitted > 0 or any(item["truncated"] for item in visible),
    }


def _state_summary(state: Any) -> dict[str, Any]:
    tasks = _value(state, "tasks")
    next_nodes = _value(state, "next")
    return {
        "checkpoint_id": str(checkpoint_id)
        if (checkpoint_id := _value(state, "checkpoint_id"))
        else None,
        "created_at": str(created_at) if (created_at := _value(state, "created_at")) else None,
        "next": [str(node) for node in next_nodes] if isinstance(next_nodes, (list, tuple)) else [],
        "task_count": len(tasks) if isinstance(tasks, (list, tuple)) else 0,
        "message_count": len(_state_messages(state)),
    }


def _latest_state_github_login(state: Mapping[str, Any] | None) -> str | None:
    messages = state.get("messages") if isinstance(state, Mapping) else None
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if _message_kind(message) not in {"human", "user"}:
            continue
        sender_id = message_sender_id(_message_content(message))
        if isinstance(sender_id, str) and sender_id.startswith("github:"):
            login = sender_id.removeprefix("github:").strip()
            return login or None
        return None
    return None


def _run_detail(run: Any) -> dict[str, Any] | None:
    if run is None:
        return None
    run_id = _value(run, "run_id") or _value(run, "id")
    status = _value(run, "status")
    return {
        "id": str(run_id) if run_id else None,
        "status": status.lower() if isinstance(status, str) else None,
        "created_at": str(created) if (created := _value(run, "created_at")) else None,
        "updated_at": str(updated) if (updated := _value(run, "updated_at")) else None,
    }


def _run_history(runs: list[Any]) -> dict[str, Any]:
    returned = [detail for run in runs[:_MAX_RUNS] if (detail := _run_detail(run)) is not None]
    return {
        "runs": returned,
        "returned_count": len(returned),
        "limit": _MAX_RUNS,
        "truncated": len(runs) > _MAX_RUNS,
    }


def _run_prepare_id(run: Any) -> str | None:
    metadata = _value(run, "metadata")
    value = metadata.get("prepare_run_id") if isinstance(metadata, Mapping) else None
    return value if isinstance(value, str) and value else None


async def _thread_cost(thread_id: str, run: Any) -> dict[str, Any]:
    run_detail = _run_detail(run)
    if run_detail and run_detail.get("status") in {"pending", "running"}:
        return {"status": "pending", "total_usd": None}
    prepare_run_id = _run_prepare_id(run)
    if not prepare_run_id:
        return {"status": "unavailable", "total_usd": None}
    try:
        snapshot = await get_langsmith_thread_cost(thread_id, prepare_run_id)
    except LangSmithCostUnavailable:
        return {"status": "unavailable", "total_usd": None}
    except Exception:
        logger.debug("Could not load thread cost for %s", thread_id, exc_info=True)
        return {"status": "unavailable", "total_usd": None}
    if snapshot is None:
        return {"status": "pending", "total_usd": None}
    return {
        "status": "available",
        "total_usd": snapshot.total_cost,
        "last_end_time": snapshot.last_end_time.isoformat(),
    }


async def _queued_message_count(client: Any, thread_id: str) -> int:
    try:
        item = await client.store.get_item(("queue", thread_id), "pending_messages")
    except Exception:
        return 0
    value = _value(item, "value")
    messages = value.get("messages") if isinstance(value, Mapping) else None
    return len(messages) if isinstance(messages, list) else 0


def _compact_plan(content: Mapping[str, Any], comments: list[dict[str, Any]]) -> dict[str, Any]:
    approved_by = content.get("approved_by")
    raw_content = content.get("html") or content.get("markdown")
    plan_content = raw_content if isinstance(raw_content, str) else None
    returned_comments = [
        {
            "id": comment.get("id"),
            "author": comment.get("author"),
            "author_login": comment.get("author_login"),
            "body": str(comment.get("body") or "")[:_MAX_DETAIL_MESSAGE_CHARS],
            "anchor": comment.get("anchor") if isinstance(comment.get("anchor"), Mapping) else None,
            "created_at": comment.get("created_at"),
        }
        for comment in comments[:_MAX_PLAN_COMMENTS]
    ]
    return {
        "status": content.get("status"),
        "format": "html"
        if content.get("html")
        else "markdown"
        if content.get("markdown")
        else None,
        "content": plan_content[:_MAX_INSPECTION_CONTENT_CHARS] if plan_content else None,
        "content_truncated": bool(
            plan_content and len(plan_content) > _MAX_INSPECTION_CONTENT_CHARS
        ),
        "comments": returned_comments,
        "comment_count": len(comments),
        "comments_truncated": len(returned_comments) < len(comments),
        "approved_by": dict(approved_by) if isinstance(approved_by, Mapping) else None,
        "approved_at": content.get("approved_at"),
    }


def _compact_approvals(approvals: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "fingerprint": item.get("fingerprint"),
            "status": item.get("status"),
            "repo": item.get("repo"),
            "branch": item.get("branch"),
            "file_count": len(item.get("files") or []),
            "approval_url": item.get("approvalUrl"),
            "requested_at": item.get("requestedAt"),
            "decided_at": item.get("decidedAt"),
            "decided_by": item.get("decidedBy"),
        }
        for item in workflow_push_approval_responses(approvals)
    ]


def _looks_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        return False
    return True


async def _authorized_locator(
    locator: str, actor: _Actor
) -> tuple[str, Mapping[str, Any]] | dict[str, Any]:
    langsmith_locator = parse_langsmith_locator(locator)
    thread_id = dashboard_thread_id(locator) or ""
    if langsmith_locator:
        thread_id = await get_open_swe_thread_id_from_langsmith(locator) or ""
        if not thread_id:
            return _failure("Could not resolve LangSmith trace to an Open SWE thread")
    if not thread_id:
        return _failure(
            "thread_id must be an exact thread ID, Open SWE dashboard URL, or LangSmith trace URL"
        )
    try:
        summary = await get_dashboard_thread(
            thread_id,
            actor.login,
            email=actor.email,
            mark_viewed=False,
        )
    except HTTPException as exc:
        if exc.status_code != 404 or langsmith_locator or not _looks_uuid(locator):
            raise
        resolved = await get_open_swe_thread_id_from_langsmith(locator)
        if not resolved or resolved == thread_id:
            raise
        thread_id = resolved
        summary = await get_dashboard_thread(
            thread_id,
            actor.login,
            email=actor.email,
            mark_viewed=False,
        )
    return thread_id, summary


def _summary_matches_pr(summary: Mapping[str, Any], locator: str) -> bool:
    expected = parse_github_pr_url(locator)
    if expected is None:
        return False
    pr = summary.get("pr")
    pull_requests = summary.get("pullRequests")
    records = [pr] if isinstance(pr, Mapping) else []
    if isinstance(pull_requests, list):
        records.extend(record for record in pull_requests if isinstance(record, Mapping))
    for record in records:
        url = record.get("url")
        actual = parse_github_pr_url(url) if isinstance(url, str) else None
        if (
            actual is not None
            and actual.owner.lower() == expected.owner.lower()
            and actual.repo.lower() == expected.repo.lower()
            and actual.number == expected.number
        ):
            return True
    return False


async def search_threads(
    query: str = "",
    participant: str | None = None,
    all_users: bool = False,
    limit: int = 25,
    offset: int = 0,
    scope: ThreadScope = "all",
    admin_threads: bool | None = None,
    state: Annotated[dict[str, Any] | None, InjectedState] = None,
) -> dict[str, Any]:
    """Search Open SWE threads by identifiers, with optional admin-thread filtering."""
    actor = await _actor(state)
    if actor is None:
        return _failure("No verified triggering user is available")
    query = query.strip()
    requested = participant.strip() if participant and participant.strip() else None
    if not query and admin_threads is not True and requested is None:
        return _failure("query, participant, or admin_threads=true is required")
    if requested and all_users:
        return _failure("participant and all_users cannot be used together")
    if admin_threads is not None and not actor.admin:
        return _failure("Only workspace admins can filter admin threads")
    cross_user = bool(requested and requested.lower() != actor.login.lower())
    if (all_users or cross_user) and not actor.admin:
        return _failure("Only workspace admins can search other users' threads")
    if scope not in {"all", "interactive", "automation"}:
        return _failure("scope must be all, interactive, or automation")

    exact_locator = parse_langsmith_locator(query) is not None or (
        dashboard_thread_id(query) is not None and "/" in query
    )
    if exact_locator or _looks_uuid(query):
        try:
            resolved = await _authorized_locator(query, actor)
        except HTTPException as exc:
            if exc.status_code != 404 or exact_locator:
                return _http_failure(exc)
        except Exception:
            logger.exception("Could not search for thread locator %s", query)
            return _failure("Could not search threads")
        else:
            if isinstance(resolved, dict):
                return resolved
            _, summary = resolved
            return {
                "success": True,
                "items": [_list_item(summary, locator=query)],
                "limit": 1,
                "offset": 0,
                "has_more": False,
            }

    pr_ref = parse_github_pr_url(query)
    normalized_query = pr_ref.url if pr_ref else query
    filter_participant_login = requested if cross_user else None
    try:
        page = await list_dashboard_threads_page(
            actor.login,
            email=actor.email,
            limit=limit,
            offset=offset,
            include_all=all_users or admin_threads is True,
            query=normalized_query,
            scope=scope,
            filter_participant_login=filter_participant_login,
            surfaced_only=True,
            admin_threads=admin_threads,
        )
    except HTTPException as exc:
        return _http_failure(exc)
    except Exception:
        logger.exception("Could not search threads")
        return _failure("Could not search threads")
    items = [item for item in page.get("items", []) if isinstance(item, Mapping)]
    if pr_ref:
        items = [item for item in items if _summary_matches_pr(item, pr_ref.url)]
    return {
        "success": True,
        "items": [_list_item(item) for item in items],
        "limit": page.get("limit"),
        "offset": page.get("offset"),
        "has_more": page.get("hasMore", False),
    }


def _available_actions(
    *,
    admin: bool,
    admin_thread: bool,
    running: bool,
    resolved: bool,
    can_delete_plan_comment: bool,
    plan: Mapping[str, Any],
    approvals: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    actions = [] if admin_thread and not admin else ["send_message"]
    plan_status = plan.get("status")
    if plan_status and plan_status not in {"approved", "cancelled", "shared"}:
        actions.append("add_plan_comment")
    if plan_status == "ready":
        actions.extend(["approve_plan", "request_plan_changes"])
    if can_delete_plan_comment:
        actions.append("delete_plan_comment")
    actions.extend(["unresolve" if resolved else "resolve", "delete"])
    if running:
        actions.append("cancel")
    if plan_status and plan_status not in {"approved", "cancelled", "shared"}:
        actions.append("update_plan")
    if any(record.get("status") == WORKFLOW_APPROVAL_PENDING for record in approvals.values()):
        actions.extend(["approve_workflow_push", "reject_workflow_push"])
    if admin and running:
        actions.append("admin_cancel")
    return actions


async def get_thread(
    thread_id: str,
    state: Annotated[dict[str, Any] | None, InjectedState] = None,
) -> dict[str, Any]:
    """Inspect a thread from its exact ID, dashboard URL, or LangSmith trace URL/run ID."""
    actor = await _actor(state)
    if actor is None:
        return _failure("No verified triggering user is available")
    locator = thread_id.strip()
    try:
        resolved = await _authorized_locator(locator, actor)
        if isinstance(resolved, dict):
            return resolved
        thread_id, summary = resolved
        client = langgraph_client()
        async with asyncio.TaskGroup() as tasks:
            thread_task = tasks.create_task(client.threads.get(thread_id))
            state_task = tasks.create_task(client.threads.get_state(thread_id))
            runs_task = tasks.create_task(client.runs.list(thread_id, limit=_MAX_RUNS + 1))
            plan_content_task = tasks.create_task(get_plan_content(thread_id))
            plan_comments_task = tasks.create_task(list_plan_comments(thread_id))
            approvals_task = tasks.create_task(get_workflow_push_approvals(thread_id))
            queued_count_task = tasks.create_task(_queued_message_count(client, thread_id))
        thread = thread_task.result()
        thread_state = state_task.result()
        runs = runs_task.result()
        plan_content = plan_content_task.result()
        plan_comments = plan_comments_task.result()
        approvals = approvals_task.result()
        queued_count = queued_count_task.result()
    except HTTPException as exc:
        return _http_failure(exc)
    except Exception:
        logger.exception("Could not load thread %s", thread_id)
        return _failure("Could not load thread")

    metadata = thread_metadata(thread)
    latest_run = runs[0] if runs else None
    plan = _compact_plan(plan_content or {}, plan_comments)
    running = summary.get("status") == "running"
    can_delete_plan_comment = any(
        comment.get("author_login") == actor.login for comment in plan_comments
    )
    cost = await _thread_cost(thread_id, latest_run)
    pr = summary.get("pr")
    pr_url = pr.get("url") if isinstance(pr, Mapping) else None
    links = {
        "web": dashboard_thread_url(thread_id),
        "plan": dashboard_plan_url(thread_id) if plan.get("status") else None,
        "trace": summary.get("traceUrl"),
        "source": summary.get("sourceUrl"),
        "pull_request": pr_url,
    }
    logins = participant_logins(metadata.get(PARTICIPANT_LOGINS_KEY))
    returned_participants = logins[:100]
    return {
        "success": True,
        "thread": _list_item(summary, locator=locator),
        "participant_logins": returned_participants,
        "participant_count": len(logins),
        "participants_truncated": len(returned_participants) < len(logins),
        "latest_run": _run_detail(latest_run),
        "recent_runs": _run_history(runs),
        "last_user_message": _last_user_message(thread_state),
        "transcript": _transcript(thread_state),
        "state": _state_summary(thread_state),
        "queued_message_count": queued_count,
        "cost": cost,
        "plan": plan,
        "workflow_approvals": _compact_approvals(approvals),
        "links": links,
        "langsmith": _langsmith_identifiers(summary.get("traceUrl"), locator),
        "available_actions": _available_actions(
            admin=actor.admin,
            admin_thread=summary.get("adminThread") is True,
            running=running,
            resolved=summary.get("resolved") is True,
            can_delete_plan_comment=can_delete_plan_comment,
            plan=plan,
            approvals=approvals,
        ),
    }


async def _send_message(
    thread_id: str,
    actor: _Actor,
    message: str,
    *,
    model_id: str | None,
    effort: str | None,
    plan_mode: bool | None,
) -> dict[str, Any]:
    if not message.strip():
        return _failure("message is required for send_message")
    if len(message) > _MAX_MESSAGE_CHARS:
        return _failure(f"message must be at most {_MAX_MESSAGE_CHARS} characters")
    if bool(model_id) != bool(effort):
        return _failure("model_id and effort must be provided together")
    if model_id and effort:
        normalized = (
            (model_id, effort)
            if model_id in SUPPORTED_MODEL_IDS and model_supports_effort(model_id, effort)
            else canonical_model_pair(model_id, effort)
        )
        if normalized is None:
            return _failure("model_id and effort are not a supported combination")
        model_id, effort = normalized

    summary = await get_dashboard_thread(
        thread_id,
        actor.login,
        email=actor.email,
        mark_viewed=False,
    )
    resolved_plan_mode = summary.get("planMode") is True if plan_mode is None else plan_mode
    body = ThreadMessageBody(
        content=message,
        model_id=model_id,
        effort=effort,
        plan_mode=resolved_plan_mode,
    )
    try:
        queued_summary = await send_dashboard_message(
            thread_id, actor.login, body, email=actor.email
        )
        return {"success": True, "mode": "queued", "thread": _list_item(queued_summary)}
    except HTTPException as exc:
        if exc.status_code != 409:
            raise

    configurable: dict[str, Any] = {"plan_mode": resolved_plan_mode}
    if model_id and effort:
        configurable.update(agent_model_id=model_id, agent_effort=effort)
    command = {
        "method": "run.start",
        "params": {
            "input": {"messages": [{"type": "human", "content": message}]},
            "config": {"configurable": configurable},
        },
    }
    status_code, content, _ = await proxy_dashboard_thread_commands(
        thread_id,
        actor.login,
        json.dumps(command).encode(),
        email=actor.email,
    )
    try:
        payload = json.loads(content) if content else None
    except json.JSONDecodeError:
        payload = None
    if status_code not in {200, 202, 204}:
        detail = payload.get("detail") if isinstance(payload, Mapping) else None
        return _failure(
            detail if isinstance(detail, str) else "Could not start thread run",
            status_code=status_code,
        )
    run_id = payload.get("run_id") if isinstance(payload, Mapping) else None
    return {
        "success": True,
        "mode": "started",
        "run_id": run_id if isinstance(run_id, str) else None,
    }


def _required(value: str | None, name: str, action: str) -> dict[str, Any] | None:
    if isinstance(value, str) and value.strip():
        return None
    return _failure(f"{name} is required for {action}")


def _unexpected_action_arguments(
    action: str,
    *,
    message: str | None,
    comment: str | None,
    comment_id: str | None,
    content: str | None,
    content_format: PlanFormat,
    fingerprint: str | None,
    confirm: bool,
    model_id: str | None,
    effort: str | None,
    plan_mode: bool | None,
) -> list[str]:
    allowed = {
        "send_message": {"message", "model_id", "effort", "plan_mode"},
        "cancel": set(),
        "admin_cancel": set(),
        "resolve": set(),
        "unresolve": set(),
        "delete": {"confirm"},
        "add_plan_comment": {"comment"},
        "delete_plan_comment": {"comment_id"},
        "update_plan": {"content", "content_format"},
        "approve_plan": set(),
        "request_plan_changes": {"comment"},
        "approve_workflow_push": {"fingerprint"},
        "reject_workflow_push": {"fingerprint"},
    }.get(action, set())
    provided = {
        key
        for key, value in {
            "message": message,
            "comment": comment,
            "comment_id": comment_id,
            "content": content,
            "fingerprint": fingerprint,
            "model_id": model_id,
            "effort": effort,
        }.items()
        if isinstance(value, str) and value.strip()
    }
    if confirm:
        provided.add("confirm")
    if plan_mode is not None:
        provided.add("plan_mode")
    if content_format != "html":
        provided.add("content_format")
    return sorted(provided - allowed)


async def manage_thread(
    thread_id: str,
    action: ThreadAction,
    message: str | None = None,
    comment: str | None = None,
    comment_id: str | None = None,
    content: str | None = None,
    content_format: PlanFormat = "html",
    fingerprint: str | None = None,
    confirm: bool = False,
    model_id: str | None = None,
    effort: str | None = None,
    plan_mode: bool | None = None,
    state: Annotated[dict[str, Any] | None, InjectedState] = None,
) -> dict[str, Any]:
    """Perform a dashboard-equivalent action on an Open SWE thread."""
    actor = await _actor(state)
    if actor is None:
        return _failure("No verified triggering user is available")
    thread_id = thread_id.strip()
    if not thread_id:
        return _failure("thread_id is required")
    unexpected = _unexpected_action_arguments(
        action,
        message=message,
        comment=comment,
        comment_id=comment_id,
        content=content,
        content_format=content_format,
        fingerprint=fingerprint,
        confirm=confirm,
        model_id=model_id,
        effort=effort,
        plan_mode=plan_mode,
    )
    if unexpected:
        return _failure(f"Unexpected arguments for {action}: {', '.join(unexpected)}")

    try:
        if action == "send_message":
            return await _send_message(
                thread_id,
                actor,
                message or "",
                model_id=model_id,
                effort=effort,
                plan_mode=plan_mode,
            )
        if action == "cancel":
            thread = await cancel_dashboard_thread(thread_id, actor.login, email=actor.email)
            return {"success": True, "thread": _list_item(thread)}
        if action == "admin_cancel":
            if not actor.admin:
                return _failure("Only workspace admins can cancel another user's thread")
            thread = await admin_cancel_dashboard_thread(thread_id)
            return {"success": True, "thread": _list_item(thread)}
        if action in {"resolve", "unresolve"}:
            thread = await resolve_dashboard_thread(
                thread_id,
                actor.login,
                resolved=action == "resolve",
                email=actor.email,
            )
            return {"success": True, "thread": _list_item(thread)}
        if action == "delete":
            if not confirm:
                return _failure("delete requires confirm=true")
            await delete_dashboard_thread(thread_id, actor.login, email=actor.email)
            return {"success": True, "deleted": True, "thread_id": thread_id}
        if action == "add_plan_comment":
            if error := _required(comment, "comment", action):
                return error
            if len(comment or "") > _MAX_COMMENT_CHARS:
                return _failure(f"comment must be at most {_MAX_COMMENT_CHARS} characters")
            result = await plan_api.post_plan_comment(
                thread_id,
                plan_api.CommentBody(body=comment or ""),
                session=actor.session,
            )
            return {"success": True, "comment": result}
        if action == "delete_plan_comment":
            if error := _required(comment_id, "comment_id", action):
                return error
            result = await plan_api.remove_plan_comment(
                thread_id,
                comment_id or "",
                session=actor.session,
            )
            return {"success": True, **result}
        if action == "update_plan":
            if error := _required(content, "content", action):
                return error
            if len(content or "") > _MAX_PLAN_CHARS:
                return _failure(f"content must be at most {_MAX_PLAN_CHARS} characters")
            existing = await get_plan_content(thread_id, raise_on_error=True) or {}
            existing_format = (
                "markdown"
                if isinstance(existing.get("markdown"), str) and not existing.get("html")
                else "html"
            )
            if content_format != existing_format:
                return _failure(f"existing plan format is {existing_format}")
            update = plan_api.PlanUpdate(**{content_format: content})
            result = await plan_api.update_plan(thread_id, update, session=actor.session)
            return {
                "success": True,
                "status": result.get("status"),
                "format": content_format,
                "content_length": len(content or ""),
                "plan_url": dashboard_plan_url(thread_id),
            }
        if action == "approve_plan":
            result = await plan_api.approve_plan(thread_id, session=actor.session)
            return {"success": True, **result}
        if action == "request_plan_changes":
            if len(comment or "") > _MAX_COMMENT_CHARS:
                return _failure(f"comment must be at most {_MAX_COMMENT_CHARS} characters")
            if comment and comment.strip():
                await plan_api.post_plan_comment(
                    thread_id,
                    plan_api.CommentBody(body=comment),
                    session=actor.session,
                )
            result = await plan_api.reject_plan(thread_id, session=actor.session)
            return {"success": True, **result}
        if action in {"approve_workflow_push", "reject_workflow_push"}:
            if error := _required(fingerprint, "fingerprint", action):
                return error
            handler = (
                workflow_approval_api.approve_workflow_push
                if action == "approve_workflow_push"
                else workflow_approval_api.reject_workflow_push
            )
            result = await handler(thread_id, fingerprint or "", session=actor.session)
            return {"success": True, **result}
        return _failure(f"unsupported action: {action}")
    except HTTPException as exc:
        return _http_failure(exc)
    except Exception:
        logger.exception("Thread action %s failed for %s", action, thread_id)
        return _failure("Thread action failed")
