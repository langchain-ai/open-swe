"""Tool: ``approve_plan``. Approve a reviewed plan and exit plan mode."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Annotated, Any, TypedDict

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


class ApprovePlanState(TypedDict, total=False):
    plan_mode: bool
    plan_approval_blocked: bool


async def approve_plan(
    state: Annotated[ApprovePlanState | None, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command | dict[str, Any]:
    """Approve the current plan and exit plan mode.

    Call this when the user approves the plan, asks to leave plan mode, or asks to
    start implementing the approved plan.
    """
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
        if isinstance(state, dict) and state.get("plan_approval_blocked") is True:
            return {
                "success": False,
                "error": "a non-owner dashboard follow-up cannot approve the plan",
            }
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


async def _thread_metadata(thread_id: str) -> dict[str, Any]:
    thread = await get_client().threads.get(thread_id)
    metadata = (
        thread.get("metadata") if isinstance(thread, dict) else getattr(thread, "metadata", None)
    )
    return metadata if isinstance(metadata, dict) else {}


def _active_plan_mode(
    state: Mapping[str, Any] | None, configurable: Any, metadata: Mapping[str, Any]
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
            "Plan mode is now inactive because the plan was approved. "
            "Implement the approved plan now. Treat this published plan as the source of truth:\n\n"
            f"{plan_markdown}"
        )
    else:
        message = (
            "Plan mode is now inactive because the plan was approved. "
            "Implement now as described in the approved plan."
        )
    if feedback:
        message += "\n\nAlso take this reviewer feedback into account:\n\n" + feedback
    return message
