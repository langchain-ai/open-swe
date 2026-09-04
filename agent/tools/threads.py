"""Discover, inspect, and manage Open SWE threads."""

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import HTTPException
from langchain_core.messages import BaseMessage
from langgraph.config import get_config
from langgraph.prebuilt import InjectedState

from agent.dashboard import plan_api
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
    get_workflow_push_approvals,
    workflow_push_approval_responses,
)
from agent.input_messages import input_message_text, message_sender_id
from agent.utils.dashboard_links import dashboard_plan_url, dashboard_thread_url
from agent.utils.json_types import as_json_object, thread_metadata
from agent.utils.langsmith import LangSmithCostUnavailable, get_langsmith_thread_cost
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
]
PlanFormat = Literal["html", "markdown"]
_MAX_MESSAGE_CHARS = 20_000
_MAX_COMMENT_CHARS = 20_000
_MAX_DETAIL_MESSAGE_CHARS = 4_000
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


def _list_item(item: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(item)
    result.pop("messages", None)
    result.pop("sandboxId", None)
    result["webUrl"] = _web_link(item)
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
    if (all_users or cross_user) and not actor.admin:
        return _failure("Only workspace admins can list other users' threads")

    filter_participant_login = requested if cross_user else None
    try:
        page = await list_dashboard_threads_page(
            actor.login,
            email=actor.email,
            limit=limit,
            offset=offset,
            include_all=all_users,
            resolved=resolved,
            viewed=viewed,
            source=source,
            status=status,
            query=query,
            scope=scope,
            automation_id=automation_id,
            filter_participant_login=filter_participant_login,
            surfaced_only=True,
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
        text = value.get("text") if isinstance(value, Mapping) else value
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


def _last_user_message(state: Any) -> dict[str, Any] | None:
    values = _value(state, "values")
    messages = values.get("messages") if isinstance(values, Mapping) else None
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
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
    return {
        "status": content.get("status"),
        "format": "html"
        if content.get("html")
        else "markdown"
        if content.get("markdown")
        else None,
        "comment_count": len(comments),
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
    if admin and running:
        actions.append("admin_cancel")
    return actions


async def get_thread(
    thread_id: str,
    state: Annotated[dict[str, Any] | None, InjectedState] = None,
) -> dict[str, Any]:
    """Get bounded operational details for one Open SWE thread."""
    actor = await _actor(state)
    if actor is None:
        return _failure("No verified triggering user is available")
    thread_id = thread_id.strip()
    if not thread_id:
        return _failure("thread_id is required")
    try:
        summary = await get_dashboard_thread(
            thread_id,
            actor.login,
            email=actor.email,
            mark_viewed=False,
        )
        client = langgraph_client()
        async with asyncio.TaskGroup() as tasks:
            thread_task = tasks.create_task(client.threads.get(thread_id))
            state_task = tasks.create_task(client.threads.get_state(thread_id))
            runs_task = tasks.create_task(client.runs.list(thread_id, limit=1))
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
        "thread": _list_item(summary),
        "participant_logins": returned_participants,
        "participant_count": len(logins),
        "participants_truncated": len(returned_participants) < len(logins),
        "latest_run": _run_detail(latest_run),
        "last_user_message": _last_user_message(thread_state),
        "queued_message_count": queued_count,
        "cost": cost,
        "plan": plan,
        "workflow_approvals": _compact_approvals(approvals),
        "links": links,
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
    """Perform a dashboard-equivalent action; allowed arguments are send_message(message, model_id, effort, plan_mode), delete(confirm), add_plan_comment(comment), delete_plan_comment(comment_id), update_plan(content, content_format), and request_plan_changes(comment)."""
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
        return _failure(f"unsupported action: {action}")
    except HTTPException as exc:
        return _http_failure(exc)
    except Exception:
        logger.exception("Thread action %s failed for %s", action, thread_id)
        return _failure("Thread action failed")
