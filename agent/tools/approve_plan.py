"""Tool: ``approve_plan``. Approve a reviewed plan and exit plan mode."""

import logging
from collections.abc import Mapping
from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from langgraph_sdk import get_client
from typing_extensions import TypedDict

from agent.dashboard.plan_store import (
    PLAN_STATUS_APPROVED,
    PLAN_STATUS_SHARED,
    format_plan_comments,
    get_plan_content,
    list_plan_comments,
    make_plan_approver,
    set_plan_status,
)
from agent.run_config import RunConfig

logger = logging.getLogger(__name__)


class ApprovePlanState(TypedDict, total=False):
    plan_mode: bool


async def approve_plan(
    state: Annotated[ApprovePlanState | None, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command | dict[str, Any]:
    """Approve the current plan and exit plan mode.

    Call this when the user approves the plan, asks to leave plan mode, or asks to
    start implementing the approved plan.
    """
    cfg = RunConfig.from_runtime()
    thread_id = cfg.thread_id
    if not thread_id:
        return {"success": False, "error": "no thread_id in run config"}

    try:
        metadata = await _thread_metadata(str(thread_id))
        if not _active_plan_mode(state, cfg, metadata):
            return {"success": False, "error": "plan mode is not active for this thread"}
        content = await get_plan_content(str(thread_id), raise_on_error=True) or {}
        if content.get("status") == PLAN_STATUS_SHARED:
            return {"success": False, "error": "shared content is not an implementation plan"}
        plan = str(content.get("html") or content.get("markdown") or "").strip()
        comments = await list_plan_comments(str(thread_id), raise_on_error=True)
        feedback = format_plan_comments(comments)
        await set_plan_status(
            str(thread_id),
            PLAN_STATUS_APPROVED,
            plan_mode=False,
            approved_by=_current_approver(cfg),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("approve_plan failed for thread %s", thread_id)
        return {"success": False, "error": f"failed to approve plan: {exc}"}

    return Command(
        update={
            "plan_mode": False,
            "messages": [
                ToolMessage(
                    content=_approved_message(plan, feedback),
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
    state: Mapping[str, Any] | None, cfg: RunConfig, metadata: Mapping[str, Any]
) -> bool:
    if isinstance(state, dict) and "plan_mode" in state:
        return state.get("plan_mode") is True
    if cfg.plan_mode is True:
        return True
    return metadata.get("plan_mode") is True


def _current_approver(cfg: RunConfig) -> dict[str, str]:
    slack_thread = cfg.slack_thread
    actor_id = (
        (slack_thread.triggering_user_id if slack_thread else "")
        or cfg.github_login
        or cfg.user_email
        or ""
    )
    name = (
        (slack_thread.triggering_user_name if slack_thread else "") or cfg.github_login or actor_id
    )
    return make_plan_approver(actor_id=actor_id, name=name, source=cfg.source or "agent")


def _approved_message(plan: str, feedback: str) -> str:
    if plan:
        message = (
            "Plan mode is now inactive because the plan was approved. Use the reviewed plan "
            "below as the implementation guide. Apply reasonable engineering judgment where "
            "details need adjustment while preserving its goals and reviewer edits:\n\n"
            f"{plan}"
        )
    else:
        message = (
            "Plan mode is now inactive because the plan was approved. "
            "Implement now as described in the approved plan."
        )
    if feedback:
        message += "\n\nAlso take this reviewer feedback into account:\n\n" + feedback
    return message
