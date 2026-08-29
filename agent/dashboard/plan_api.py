"""REST API for HTML plan artifacts, comments, approval, and change requests."""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from langgraph_sdk import get_client
from langgraph_sdk.schema import Run
from pydantic import BaseModel, Field, model_validator

from agent.input_messages import build_system_run_input
from agent.source_context import SourceContext

from ..dispatch import dispatch_agent_run
from ..utils.slack import post_slack_thread_reply
from .oauth import require_same_origin_for_mutations, require_session
from .plan_store import (
    PLAN_STATUS_APPROVED,
    PLAN_STATUS_CANCELLED,
    PLAN_STATUS_READY,
    PLAN_STATUS_REVISING,
    PLAN_STATUS_SHARED,
    add_plan_comment,
    delete_plan_comment,
    format_plan_comments,
    get_plan_content,
    list_plan_comments,
    make_plan_approver,
    plan_file_path_for_thread,
    save_plan_content,
    set_plan_status,
    write_plan_to_sandbox,
)
from .thread_api import (
    _repo_config_from_metadata,
    _thread_is_readable,
    _thread_source,
)

logger = logging.getLogger(__name__)
_plan_approval_locks: dict[str, asyncio.Lock] = {}

plan_router = APIRouter(
    prefix="/dashboard/api/plan",
    tags=["plan"],
    dependencies=[Depends(require_same_origin_for_mutations)],
)
_SESSION_DEP = Depends(require_session)


class TextAnchor(BaseModel):
    exact: str = Field(min_length=1, max_length=1000)
    prefix: str = Field(max_length=64)
    suffix: str = Field(max_length=64)
    context_before: str = Field(default="", max_length=1000)
    context_after: str = Field(default="", max_length=1000)
    start: int = Field(ge=0, le=2_000_000)
    end: int = Field(gt=0, le=2_000_000)

    @model_validator(mode="after")
    def validate_range(self) -> "TextAnchor":
        if self.end <= self.start or self.end - self.start != len(self.exact):
            raise ValueError("anchor range must match the selected text")
        return self


class CommentBody(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)
    anchor: TextAnchor | None = None


class PlanUpdate(BaseModel):
    html: str | None = None
    markdown: str | None = None


class PlanRejection(BaseModel):
    dispatch: bool = True


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
    approved_by = content.get("approved_by") or metadata.get("plan_approved_by")
    if isinstance(approved_by, dict):
        approved_by = make_plan_approver(
            actor_id=str(approved_by.get("id") or ""),
            name=str(approved_by.get("name") or ""),
            source=str(approved_by.get("source") or ""),
        )
    else:
        approved_by = None
    approved_at = content.get("approved_at") or metadata.get("plan_approved_at")
    return {
        "threadId": thread_id,
        "status": content.get("status") or metadata.get("plan_status") or "planning",
        "html": content.get("html", ""),
        "markdown": content.get("markdown", ""),
        "approvedBy": approved_by,
        "approvedAt": approved_at if isinstance(approved_at, str) else None,
        "user": {
            "id": login,
            "login": login,
            "email": email,
            "name": session.get("name") or login,
        },
    }


@plan_router.put("/{thread_id}")
async def update_plan(
    thread_id: str, body: PlanUpdate, session: dict[str, Any] = _SESSION_DEP
) -> dict[str, Any]:
    """Save an edited HTML artifact while preserving review comments."""
    metadata = await _thread_metadata(thread_id)
    if not _thread_is_readable(metadata):
        raise HTTPException(404, "thread not found")
    content = await get_plan_content(thread_id) or {}
    _reject_shared_content(content)
    legacy_markdown = isinstance(content.get("markdown"), str) and not content.get("html")
    field = "markdown" if legacy_markdown else "html"
    value = getattr(body, field)
    value = value.strip() if isinstance(value, str) else ""
    if not value:
        raise HTTPException(422, f"plan {field} cannot be empty")
    status = content.get("status") or metadata.get("plan_status") or "planning"
    if status in (PLAN_STATUS_APPROVED, PLAN_STATUS_CANCELLED):
        raise HTTPException(409, f"cannot edit a {status} plan")
    plan_file_path = content.get("plan_file_path")
    plan_file_path = (
        plan_file_path if isinstance(plan_file_path, str) else plan_file_path_for_thread(thread_id)
    )
    if legacy_markdown:
        await save_plan_content(
            thread_id,
            markdown=value,
            status=PLAN_STATUS_READY,
            clear_comments=False,
            plan_file_path=plan_file_path,
        )
    else:
        await save_plan_content(
            thread_id,
            html=value,
            status=PLAN_STATUS_READY,
            clear_comments=False,
            plan_file_path=plan_file_path,
        )
    await write_plan_to_sandbox(thread_id, value, plan_file_path=plan_file_path)
    return {"status": PLAN_STATUS_READY, field: value}


@plan_router.get("/{thread_id}/comments")
async def get_plan_comments(
    thread_id: str, session: dict[str, Any] = _SESSION_DEP
) -> dict[str, Any]:
    metadata = await _thread_metadata(thread_id)
    if not _thread_is_readable(metadata):
        raise HTTPException(404, "thread not found")
    return {"comments": await list_plan_comments(thread_id)}


@plan_router.post("/{thread_id}/comments")
async def post_plan_comment(
    thread_id: str, body: CommentBody, session: dict[str, Any] = _SESSION_DEP
) -> dict[str, Any]:
    metadata = await _thread_metadata(thread_id)
    if not _thread_is_readable(metadata):
        raise HTTPException(404, "thread not found")
    _reject_shared_content(await get_plan_content(thread_id) or {})
    text = body.body.strip()
    if not text:
        raise HTTPException(422, "comment body cannot be empty")
    login = session["sub"]
    return await add_plan_comment(
        thread_id,
        author=session.get("name") or login,
        author_login=login,
        body=text,
        anchor=body.anchor.model_dump() if body.anchor else None,
    )


@plan_router.delete("/{thread_id}/comments/{comment_id}")
async def remove_plan_comment(
    thread_id: str, comment_id: str, session: dict[str, Any] = _SESSION_DEP
) -> dict[str, Any]:
    metadata = await _thread_metadata(thread_id)
    if not _thread_is_readable(metadata):
        raise HTTPException(404, "thread not found")
    _reject_shared_content(await get_plan_content(thread_id) or {})
    comments = await list_plan_comments(thread_id)
    target = next((c for c in comments if c.get("id") == comment_id), None)
    if target is None:
        raise HTTPException(404, "comment not found")
    login = session["sub"]
    if target.get("author_login") != login:
        raise HTTPException(403, "only the comment author can delete a comment")
    await delete_plan_comment(thread_id, comment_id)
    return {"ok": True}


@plan_router.post("/{thread_id}/approve")
async def approve_plan(thread_id: str, session: dict[str, Any] = _SESSION_DEP) -> dict[str, Any]:
    metadata = await _thread_metadata(thread_id)
    if not _thread_is_readable(metadata):
        raise HTTPException(404, "thread not found")
    actor_id = str(session.get("sub") or "").strip()
    return await approve_plan_for_thread(
        thread_id,
        approver=make_plan_approver(
            actor_id=actor_id,
            name=_approval_actor_name(session),
            source="dashboard",
        ),
    )


async def approve_plan_for_thread(thread_id: str, *, approver: dict[str, str]) -> dict[str, Any]:
    approver = make_plan_approver(
        actor_id=str(approver.get("id") or ""),
        name=str(approver.get("name") or ""),
        source=str(approver.get("source") or ""),
    )
    lock = _plan_approval_locks.setdefault(thread_id, asyncio.Lock())
    async with lock:
        metadata = await _thread_metadata(thread_id)
        content = await get_plan_content(thread_id, raise_on_error=True) or {}
        _reject_shared_content(content)
        if (
            metadata.get("plan_mode") is not True
            or metadata.get("plan_status") != PLAN_STATUS_READY
            or content.get("status") != PLAN_STATUS_READY
        ):
            return {
                "status": str(content.get("status") or metadata.get("plan_status") or "planning"),
                "already_approved": True,
            }
        plan_html = str(content.get("html", "")).strip()
        plan_markdown = str(content.get("markdown", "")).strip()
        comments = await list_plan_comments(thread_id, raise_on_error=True)
        feedback = format_plan_comments(comments)
        await set_plan_status(
            thread_id,
            PLAN_STATUS_APPROVED,
            plan_mode=False,
            approved_by=approver,
        )
        if plan_html:
            text = (
                "The plan has been approved. Use the reviewed self-contained HTML artifact below "
                "as the implementation guide. Apply reasonable engineering judgment where details "
                f"need adjustment while preserving its goals and reviewer edits:\n\n{plan_html}"
            )
        elif plan_markdown:
            text = (
                "The plan has been approved. Use the reviewed Markdown plan below as the "
                "implementation guide. Apply reasonable engineering judgment where details need "
                f"adjustment while preserving its goals and reviewer edits:\n\n{plan_markdown}"
            )
        else:
            text = "The plan has been approved. Implement it now as described in the plan."
        if feedback:
            text += "\n\nAlso take this reviewer feedback into account:\n\n" + feedback
        try:
            run = await _dispatch_followup(thread_id, metadata, text, plan_mode=False)
        except Exception:
            await set_plan_status(thread_id, PLAN_STATUS_READY, plan_mode=True)
            raise
        await _maybe_post_plan_approved_to_slack(
            metadata,
            thread_id=thread_id,
            comment_count=len(comments),
            actor=approver["name"],
        )
        return {"status": PLAN_STATUS_APPROVED, "run_id": run["run_id"]}


@plan_router.post("/{thread_id}/reject")
async def reject_plan(
    thread_id: str,
    rejection: PlanRejection | None = None,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    metadata = await _thread_metadata(thread_id)
    if not _thread_is_readable(metadata):
        raise HTTPException(404, "thread not found")
    lock = _plan_approval_locks.setdefault(thread_id, asyncio.Lock())
    async with lock:
        metadata = await _thread_metadata(thread_id)
        content = await get_plan_content(thread_id, raise_on_error=True) or {}
        _reject_shared_content(content)
        if (
            metadata.get("plan_status") != PLAN_STATUS_READY
            or content.get("status") != PLAN_STATUS_READY
        ):
            raise HTTPException(409, "plan is no longer ready for review")
        await set_plan_status(thread_id, PLAN_STATUS_REVISING, plan_mode=True)
    if rejection is not None and not rejection.dispatch:
        return {"status": PLAN_STATUS_REVISING}
    feedback = format_plan_comments(await list_plan_comments(thread_id, raise_on_error=True))
    text = (
        "The plan needs changes before implementation. Address this reviewer feedback in the "
        "existing self-contained HTML file under /workspace/plans/, then publish an updated "
        f"artifact with the save_plan tool:\n\n{feedback or '(no specific comments were left)'}"
    )
    await _dispatch_followup(thread_id, metadata, text, plan_mode=True)
    return {"status": PLAN_STATUS_REVISING}


def _reject_shared_content(content: dict[str, Any]) -> None:
    if content.get("status") == PLAN_STATUS_SHARED:
        raise HTTPException(409, "shared content is not an implementation plan")


def _approval_actor_name(session: dict[str, Any]) -> str:
    actor = session.get("name") or session.get("sub") or "User"
    return str(actor).strip() or "User"


def _slack_thread_from_metadata(metadata: dict[str, Any]) -> tuple[str, str] | None:
    slack_thread = SourceContext.from_metadata(metadata).slack_thread
    if slack_thread is None:
        return None
    channel_id = slack_thread.channel_id
    thread_ts = slack_thread.thread_ts
    if not isinstance(channel_id, str) or not channel_id.strip():
        return None
    if not isinstance(thread_ts, str) or not thread_ts.strip():
        return None
    return channel_id.strip(), thread_ts.strip()


def _plan_approved_slack_text(comment_count: int, actor: str) -> str:
    return f"Plan approved with {comment_count} comments by {actor}"


def _plan_approved_slack_blocks(text: str) -> list[dict[str, Any]]:
    return [{"type": "context", "elements": [{"type": "mrkdwn", "text": f"_{text}_"}]}]


async def _maybe_post_plan_approved_to_slack(
    metadata: dict[str, Any], *, thread_id: str, comment_count: int, actor: str
) -> None:
    slack_thread = _slack_thread_from_metadata(metadata)
    if slack_thread is None:
        return
    channel_id, thread_ts = slack_thread
    text = _plan_approved_slack_text(comment_count, actor)
    try:
        ok = await post_slack_thread_reply(
            channel_id,
            thread_ts,
            text,
            blocks=_plan_approved_slack_blocks(text),
            agent_thread_id=thread_id,
        )
    except Exception:
        logger.warning("Could not post plan approval Slack reply", exc_info=True)
        return
    if not ok:
        logger.warning("Could not post plan approval Slack reply to %s/%s", channel_id, thread_ts)


async def _dispatch_followup(
    thread_id: str, metadata: dict[str, Any], text: str, *, plan_mode: bool
) -> Run:
    """Continue the existing thread with the decision as a new instruction run."""
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
    context = SourceContext.from_metadata(metadata)
    if context.slack_thread is not None:
        configurable["slack_thread"] = context.dump()["slack_thread"]
    configurable["plan_mode"] = plan_mode

    return await dispatch_agent_run(
        thread_id,
        configurable,
        source=configurable["source"],
        input=build_system_run_input(
            text,
            sender_id="system:plan-review",
            display_name="Plan review",
        ),
    )
