"""REST API for the plan-review page: read the plan, approve, or request changes.

Comment threads themselves live in the collaborative Yjs document (BlockNote's
``YjsThreadStore``) and are harvested client-side; on approve/reject the client
sends the harvested feedback here, where it is formatted and handed to the agent
as the instruction for the follow-up run. The agent never sees comments during
review — only this aggregated feedback at the decision point.

Permissions: any authenticated org member can read a surfaced thread and request
changes (reject); only the thread owner can approve.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from langgraph_sdk import get_client
from pydantic import BaseModel, Field

from .oauth import require_same_origin_for_mutations, require_session
from .plan_store import (
    PLAN_STATUS_APPROVED,
    PLAN_STATUS_REVISING,
    get_plan_content,
    set_plan_status,
)
from .thread_api import (
    _repo_config_from_metadata,
    _thread_is_readable,
    _thread_source,
    _user_owns_thread,
)

logger = logging.getLogger(__name__)

plan_router = APIRouter(
    prefix="/dashboard/api/plan",
    tags=["plan"],
    dependencies=[Depends(require_same_origin_for_mutations)],
)
_SESSION_DEP = Depends(require_session)


class PlanComment(BaseModel):
    author: str | None = None
    body: str = ""
    quote: str | None = None
    resolved: bool = False


class PlanDecisionBody(BaseModel):
    comments: list[PlanComment] = Field(default_factory=list)


async def _thread_metadata(thread_id: str) -> dict[str, Any]:
    client = get_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc
    metadata = (
        thread.get("metadata") if isinstance(thread, dict) else getattr(thread, "metadata", None)
    )
    return metadata if isinstance(metadata, dict) else {}


@plan_router.get("/{thread_id}")
async def get_plan(thread_id: str, session: dict[str, Any] = _SESSION_DEP) -> dict[str, Any]:
    metadata = await _thread_metadata(thread_id)
    if not _thread_is_readable(metadata):
        raise HTTPException(404, "thread not found")
    login = session["sub"]
    email = session.get("email")
    content = await get_plan_content(thread_id) or {}
    return {
        "threadId": thread_id,
        "status": content.get("status") or metadata.get("plan_status") or "planning",
        "markdown": content.get("markdown", ""),
        "isOwner": _user_owns_thread(metadata, login, email),
        "user": {
            "id": login,
            "login": login,
            "email": email,
            "name": session.get("name") or login,
        },
    }


@plan_router.post("/{thread_id}/approve")
async def approve_plan(
    thread_id: str,
    body: PlanDecisionBody,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    metadata = await _thread_metadata(thread_id)
    if not _user_owns_thread(metadata, session["sub"], session.get("email")):
        raise HTTPException(403, "only the plan owner can approve")
    await set_plan_status(thread_id, PLAN_STATUS_APPROVED, plan_mode=False)
    feedback = _format_comments(body.comments)
    if feedback:
        text = (
            "The plan has been approved. Implement it now, taking this reviewer "
            f"feedback into account:\n\n{feedback}"
        )
    else:
        text = "The plan has been approved. Implement it now as described in the plan."
    await _dispatch_followup(thread_id, metadata, text, plan_mode=False)
    return {"status": PLAN_STATUS_APPROVED}


@plan_router.post("/{thread_id}/reject")
async def reject_plan(
    thread_id: str,
    body: PlanDecisionBody,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    metadata = await _thread_metadata(thread_id)
    if not _thread_is_readable(metadata):
        raise HTTPException(404, "thread not found")
    await set_plan_status(thread_id, PLAN_STATUS_REVISING, plan_mode=True)
    feedback = _format_comments(body.comments)
    text = (
        "The plan needs changes before implementation. Address this reviewer "
        "feedback and publish an updated plan with the save_plan tool:\n\n"
        f"{feedback or '(no specific comments were left)'}"
    )
    await _dispatch_followup(thread_id, metadata, text, plan_mode=True)
    return {"status": PLAN_STATUS_REVISING}


def _format_comments(comments: list[PlanComment]) -> str:
    lines: list[str] = []
    for index, comment in enumerate(comments, start=1):
        body = comment.body.strip()
        if not body:
            continue
        author = (comment.author or "reviewer").strip()
        quote = (comment.quote or "").strip()
        status = " (resolved)" if comment.resolved else ""
        if quote:
            lines.append(f'{index}. On "{quote}"{status}:\n   - {author}: {body}')
        else:
            lines.append(f"{index}. {author}{status}: {body}")
    return "\n".join(lines)


async def _dispatch_followup(
    thread_id: str, metadata: dict[str, Any], text: str, *, plan_mode: bool
) -> None:
    """Continue the existing thread with a new instruction run.

    Runs on the same LangGraph thread, so the agent resumes from the checkpoint
    with the full planning history plus this instruction. The configurable is
    rebuilt from the thread's stored owner/repo/Slack context so the agent can
    push, open a PR, and reply in the original channel.
    """
    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "source": _thread_source(metadata) or "slack",
    }
    email = metadata.get("triggering_user_email")
    if isinstance(email, str) and email:
        configurable["user_email"] = email
    login = metadata.get("github_login")
    if isinstance(login, str) and login:
        configurable["github_login"] = login
    repo = _repo_config_from_metadata(metadata)
    if repo:
        configurable["repo"] = repo
    source_context = metadata.get("source_context")
    if isinstance(source_context, dict):
        slack_thread = source_context.get("slack_thread")
        if isinstance(slack_thread, dict):
            configurable["slack_thread"] = slack_thread
    # Carry the decision to the follow-up run: approve continues out of plan
    # mode (implement), reject stays in plan mode (revise the plan).
    configurable["plan_mode"] = plan_mode

    client = get_client()
    await client.runs.create(
        thread_id,
        "agent",
        input={"messages": [{"role": "user", "content": text}]},
        config={"configurable": configurable},
        if_not_exists="create",
    )
