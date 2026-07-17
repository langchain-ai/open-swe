"""Tool: ``approve_plan``. Approve a reviewed plan and exit plan mode."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId
from langgraph.config import get_config
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from langgraph_sdk import get_client

from ..dashboard.plan_store import (
    PLAN_STATUS_APPROVED,
    PLAN_STATUS_SHARED,
    get_plan_content,
    list_plan_comments,
    set_plan_status,
)
from ..dashboard.thread_api import _user_owns_thread

logger = logging.getLogger(__name__)

_EXPLICIT_APPROVAL_PHRASES = (
    "approve",
    "approve it",
    "approve plan",
    "approve the plan",
    "approved",
    "exit plan mode",
    "leave plan mode",
    "get out of plan mode",
    "turn off plan mode",
    "disable plan mode",
    "implement now",
    "implement the plan",
    "start implementation",
    "proceed with implementation",
    "continue implementation",
    "continue with implementation",
    "go ahead and implement",
    "go ahead and implement it",
)
_EXPLICIT_APPROVAL_NEGATIONS = (
    "cancel",
    "change",
    "changes",
    "deny",
    "denied",
    "do not",
    "don t",
    "dont",
    "hasn t",
    "hasnt",
    "haven t",
    "havent",
    "hold",
    "no",
    "not",
    "reject",
    "revise",
    "stop",
    "wait",
)


async def approve_plan(
    approval_request: str,
    state: Annotated[dict[str, Any] | None, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command | dict[str, Any]:
    """Approve the current plan and exit plan mode only when explicitly asked.

    Call this only when the latest user message explicitly asks you to approve the
    current plan, exit/disable plan mode, or begin implementation of the plan. Do
    not call it for vague approval, praise, silence, or your own judgment. Pass
    the exact user request that explicitly authorizes approval.
    """
    if not isinstance(approval_request, str) or not approval_request.strip():
        return {"success": False, "error": "approval_request must quote the explicit user ask"}
    if not _is_explicit_plan_approval_request(approval_request):
        return {
            "success": False,
            "error": "approval_request is not an explicit request to approve or exit plan mode",
        }

    try:
        config = get_config()
    except Exception:
        config = {}
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    if not thread_id:
        return {"success": False, "error": "no thread_id in run config"}

    try:
        metadata = await _thread_metadata(str(thread_id))
        if not _active_plan_mode(state, configurable, metadata):
            return {"success": False, "error": "plan mode is not active for this thread"}
        if not _current_user_owns_thread(metadata, configurable):
            return {"success": False, "error": "only the plan owner can approve the plan"}
        content = await get_plan_content(str(thread_id), raise_on_error=True) or {}
        if content.get("status") == PLAN_STATUS_SHARED:
            return {"success": False, "error": "shared content is not an implementation plan"}
        plan_markdown = str(content.get("markdown", "")).strip()
        comments = await list_plan_comments(str(thread_id), raise_on_error=True)
        feedback = _format_comments(comments)
        await set_plan_status(str(thread_id), PLAN_STATUS_APPROVED, plan_mode=False)
    except Exception as exc:  # noqa: BLE001
        logger.exception("approve_plan failed for thread %s", thread_id)
        return {"success": False, "error": f"failed to approve plan: {exc}"}

    return Command(
        update={
            "plan_mode": False,
            "messages": [
                ToolMessage(
                    content=_approved_message(plan_markdown, feedback),
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


def _is_explicit_plan_approval_request(text: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    if not normalized:
        return False
    padded = f" {normalized} "
    if any(f" {phrase} " in padded for phrase in _EXPLICIT_APPROVAL_NEGATIONS):
        return False
    return any(f" {phrase} " in padded for phrase in _EXPLICIT_APPROVAL_PHRASES)


async def _thread_metadata(thread_id: str) -> dict[str, Any]:
    thread = await get_client().threads.get(thread_id)
    metadata = (
        thread.get("metadata") if isinstance(thread, dict) else getattr(thread, "metadata", None)
    )
    return metadata if isinstance(metadata, dict) else {}


def _active_plan_mode(
    state: dict[str, Any] | None, configurable: Any, metadata: Mapping[str, Any]
) -> bool:
    if isinstance(state, dict) and "plan_mode" in state:
        return state.get("plan_mode") is True
    if isinstance(configurable, dict) and configurable.get("plan_mode") is True:
        return True
    return metadata.get("plan_mode") is True


def _current_user_owns_thread(metadata: Mapping[str, Any], configurable: Any) -> bool:
    if not isinstance(configurable, dict):
        return False
    login = configurable.get("github_login")
    login = login.strip() if isinstance(login, str) else ""
    email = configurable.get("user_email")
    if not isinstance(email, str):
        slack_thread = configurable.get("slack_thread")
        if isinstance(slack_thread, dict):
            email = slack_thread.get("triggering_user_email")
    email = email.strip().lower() if isinstance(email, str) else None
    return bool(login or email) and _user_owns_thread(metadata, login, email)


def _format_comments(comments: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    index = 1
    for comment in comments:
        body = str(comment.get("body", "")).strip()
        if not body:
            continue
        author = str(comment.get("author") or "reviewer").strip()
        lines.append(f"{index}. {author}: {body}")
        index += 1
    return "\n".join(lines)


def _approved_message(plan_markdown: str, feedback: str) -> str:
    if plan_markdown:
        message = (
            "Plan mode is now inactive because the plan owner explicitly approved it. "
            "Implement the approved plan now. Treat this published plan as the source of truth:\n\n"
            f"{plan_markdown}"
        )
    else:
        message = (
            "Plan mode is now inactive because the plan owner explicitly approved it. "
            "Implement now as described in the approved plan."
        )
    if feedback:
        message += "\n\nAlso take this reviewer feedback into account:\n\n" + feedback
    return message
