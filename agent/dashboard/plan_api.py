"""REST API for the plan-review page: read the plan, comment, approve, or request
changes — all plain HTTP, no CRDT/WebSocket.

Reviewers leave whole-document comments via this API; they're stored server-side
and listed for everyone who can read the thread. On approve/reject the comments
are read back here, formatted, and handed to the agent as the instruction for the
follow-up run. The agent never sees comments during review — only this aggregated
feedback at the decision point.

Permissions: any authenticated org member can read a surfaced thread, comment, and
request changes (reject); only the thread owner can approve. A comment can be
deleted by its author or the thread owner.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from langgraph_sdk import get_client
from pydantic import BaseModel

from ..dispatch import dispatch_agent_run
from ..utils.slack import post_slack_thread_reply
from .oauth import require_same_origin_for_mutations, require_session
from .plan_store import (
    PLAN_STATUS_APPROVED,
    PLAN_STATUS_CANCELLED,
    PLAN_STATUS_READY,
    PLAN_STATUS_REVISING,
    add_plan_comment,
    delete_plan_comment,
    get_plan_content,
    list_plan_comments,
    save_plan_content,
    set_plan_status,
    write_plan_to_sandbox,
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


class CommentBody(BaseModel):
    body: str


class PlanUpdate(BaseModel):
    markdown: str


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


@plan_router.put("/{thread_id}")
async def update_plan(
    thread_id: str, body: PlanUpdate, session: dict[str, Any] = _SESSION_DEP
) -> dict[str, Any]:
    """Owner-only manual edit of the plan markdown.

    Re-publishes the edited plan as ``ready`` (and mirrors it into the sandbox
    ``plan.md``) while preserving reviewer comments, so the owner can refine the
    plan before approving it."""
    metadata = await _thread_metadata(thread_id)
    if not _user_owns_thread(metadata, session["sub"], session.get("email")):
        raise HTTPException(403, "only the plan owner can edit the plan")
    markdown = body.markdown.strip()
    if not markdown:
        raise HTTPException(422, "plan markdown cannot be empty")
    content = await get_plan_content(thread_id) or {}
    status = content.get("status") or metadata.get("plan_status") or "planning"
    if status in (PLAN_STATUS_APPROVED, PLAN_STATUS_CANCELLED):
        raise HTTPException(409, f"cannot edit a {status} plan")
    await save_plan_content(
        thread_id, markdown=markdown, status=PLAN_STATUS_READY, clear_comments=False
    )
    await write_plan_to_sandbox(thread_id, markdown)
    return {"status": PLAN_STATUS_READY, "markdown": markdown}


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
    text = body.body.strip()
    if not text:
        raise HTTPException(422, "comment body cannot be empty")
    login = session["sub"]
    return await add_plan_comment(
        thread_id, author=session.get("name") or login, author_login=login, body=text
    )


@plan_router.delete("/{thread_id}/comments/{comment_id}")
async def remove_plan_comment(
    thread_id: str, comment_id: str, session: dict[str, Any] = _SESSION_DEP
) -> dict[str, Any]:
    metadata = await _thread_metadata(thread_id)
    if not _thread_is_readable(metadata):
        raise HTTPException(404, "thread not found")
    comments = await list_plan_comments(thread_id)
    target = next((c for c in comments if c.get("id") == comment_id), None)
    if target is None:
        raise HTTPException(404, "comment not found")
    login = session["sub"]
    is_owner = _user_owns_thread(metadata, login, session.get("email"))
    if target.get("author_login") != login and not is_owner:
        raise HTTPException(403, "only the author or the plan owner can delete a comment")
    await delete_plan_comment(thread_id, comment_id)
    return {"ok": True}


@plan_router.post("/{thread_id}/approve")
async def approve_plan(thread_id: str, session: dict[str, Any] = _SESSION_DEP) -> dict[str, Any]:
    metadata = await _thread_metadata(thread_id)
    if not _user_owns_thread(metadata, session["sub"], session.get("email")):
        raise HTTPException(403, "only the plan owner can approve")
    # Read the published plan + comments BEFORE mutating state: a store failure
    # here aborts the decision (500) rather than dispatching without them. The
    # published markdown may have been edited by the reviewer, so it is the
    # source of truth handed to the agent (not its own stale history) — read it
    # strictly so a transient failure can't silently drop the edit.
    content = await get_plan_content(thread_id, raise_on_error=True) or {}
    plan_markdown = str(content.get("markdown", "")).strip()
    comments = await list_plan_comments(thread_id, raise_on_error=True)
    feedback = _format_comments(comments)
    await set_plan_status(thread_id, PLAN_STATUS_APPROVED, plan_mode=False)
    if plan_markdown:
        text = (
            "The plan has been approved. Implement it now exactly as written "
            "below (it may have been edited by the reviewer, so treat this as "
            f"the source of truth):\n\n{plan_markdown}"
        )
    else:
        text = "The plan has been approved. Implement it now as described in the plan."
    if feedback:
        text += "\n\nAlso take this reviewer feedback into account:\n\n" + feedback
    await _dispatch_followup(thread_id, metadata, text, plan_mode=False)
    await _maybe_post_plan_approved_to_slack(
        metadata,
        comment_count=len(comments),
        actor=_approval_actor_name(session),
    )
    return {"status": PLAN_STATUS_APPROVED}


@plan_router.post("/{thread_id}/reject")
async def reject_plan(thread_id: str, session: dict[str, Any] = _SESSION_DEP) -> dict[str, Any]:
    metadata = await _thread_metadata(thread_id)
    if not _thread_is_readable(metadata):
        raise HTTPException(404, "thread not found")
    feedback = _format_comments(await list_plan_comments(thread_id, raise_on_error=True))
    await set_plan_status(thread_id, PLAN_STATUS_REVISING, plan_mode=True)
    text = (
        "The plan needs changes before implementation. Address this reviewer "
        "feedback and publish an updated plan with the save_plan tool:\n\n"
        f"{feedback or '(no specific comments were left)'}"
    )
    await _dispatch_followup(thread_id, metadata, text, plan_mode=True)
    return {"status": PLAN_STATUS_REVISING}


def _approval_actor_name(session: dict[str, Any]) -> str:
    actor = session.get("name") or session.get("sub") or "User"
    return str(actor).strip() or "User"


def _slack_thread_from_metadata(metadata: dict[str, Any]) -> tuple[str, str] | None:
    source_context = metadata.get("source_context")
    if not isinstance(source_context, dict):
        return None
    slack_thread = source_context.get("slack_thread")
    if not isinstance(slack_thread, dict):
        return None
    channel_id = slack_thread.get("channel_id")
    thread_ts = slack_thread.get("thread_ts")
    if not isinstance(channel_id, str) or not channel_id.strip():
        return None
    if not isinstance(thread_ts, str) or not thread_ts.strip():
        return None
    return channel_id.strip(), thread_ts.strip()


def _plan_approved_slack_text(comment_count: int, actor: str) -> str:
    return f"Plan approved with {comment_count} comments by {actor}\nbeginning implementation"


async def _maybe_post_plan_approved_to_slack(
    metadata: dict[str, Any], *, comment_count: int, actor: str
) -> None:
    slack_thread = _slack_thread_from_metadata(metadata)
    if slack_thread is None:
        return
    channel_id, thread_ts = slack_thread
    try:
        ok = await post_slack_thread_reply(
            channel_id,
            thread_ts,
            _plan_approved_slack_text(comment_count, actor),
        )
    except Exception:
        logger.warning("Could not post plan approval Slack reply", exc_info=True)
        return
    if not ok:
        logger.warning("Could not post plan approval Slack reply to %s/%s", channel_id, thread_ts)


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

    await dispatch_agent_run(
        thread_id,
        text,
        configurable,
        source=configurable["source"],
    )
