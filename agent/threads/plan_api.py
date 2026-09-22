"""REST API for published HTML artifacts and comments."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from langgraph_sdk import get_client
from pydantic import BaseModel, Field, model_validator

from agent.dashboard.oauth import require_same_origin_for_mutations, require_session
from agent.threads.plan_store import (
    PLAN_STATUS_SHARED,
    add_plan_comment,
    delete_plan_comment,
    get_plan_content,
    list_plan_comments,
    make_plan_approver,
    plan_file_path_for_thread,
    save_plan_content,
    write_plan_to_sandbox,
)
from agent.threads.summary import (
    thread_is_promptable,
    thread_is_readable,
)

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
    def validate_range(self) -> TextAnchor:
        if self.end <= self.start or self.end - self.start != len(self.exact):
            raise ValueError("anchor range must match the selected text")
        return self


class CommentBody(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)
    anchor: TextAnchor | None = None


class PlanUpdate(BaseModel):
    html: str | None = None
    markdown: str | None = None


async def fetch_thread_metadata(thread_id: str) -> dict[str, Any]:
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
    metadata = await fetch_thread_metadata(thread_id)
    if not thread_is_readable(metadata, session["sub"], session.get("email")):
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
        "status": content.get("status") or metadata.get("plan_status") or PLAN_STATUS_SHARED,
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
    metadata = await fetch_thread_metadata(thread_id)
    if not thread_is_promptable(metadata, session["sub"]):
        raise HTTPException(404, "thread not found")
    content = await get_plan_content(thread_id) or {}
    legacy_markdown = isinstance(content.get("markdown"), str) and not content.get("html")
    field = "markdown" if legacy_markdown else "html"
    value = getattr(body, field)
    value = value.strip() if isinstance(value, str) else ""
    if not value:
        raise HTTPException(422, f"plan {field} cannot be empty")
    plan_file_path = content.get("plan_file_path")
    plan_file_path = (
        plan_file_path if isinstance(plan_file_path, str) else plan_file_path_for_thread(thread_id)
    )
    if legacy_markdown:
        await save_plan_content(
            thread_id,
            markdown=value,
            status=PLAN_STATUS_SHARED,
            clear_comments=False,
            plan_file_path=plan_file_path,
        )
    else:
        await save_plan_content(
            thread_id,
            html=value,
            status=PLAN_STATUS_SHARED,
            clear_comments=False,
            plan_file_path=plan_file_path,
        )
    await write_plan_to_sandbox(thread_id, value, plan_file_path=plan_file_path)
    return {"status": PLAN_STATUS_SHARED, field: value}


@plan_router.get("/{thread_id}/comments")
async def get_plan_comments(
    thread_id: str, session: dict[str, Any] = _SESSION_DEP
) -> dict[str, Any]:
    metadata = await fetch_thread_metadata(thread_id)
    if not thread_is_readable(metadata, session["sub"], session.get("email")):
        raise HTTPException(404, "thread not found")
    return {"comments": await list_plan_comments(thread_id)}


@plan_router.post("/{thread_id}/comments")
async def post_plan_comment(
    thread_id: str, body: CommentBody, session: dict[str, Any] = _SESSION_DEP
) -> dict[str, Any]:
    metadata = await fetch_thread_metadata(thread_id)
    if not thread_is_promptable(metadata, session["sub"]):
        raise HTTPException(404, "thread not found")
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
    metadata = await fetch_thread_metadata(thread_id)
    if not thread_is_promptable(metadata, session["sub"]):
        raise HTTPException(404, "thread not found")
    comments = await list_plan_comments(thread_id)
    target = next((c for c in comments if c.get("id") == comment_id), None)
    if target is None:
        raise HTTPException(404, "comment not found")
    login = session["sub"]
    if target.get("author_login") != login:
        raise HTTPException(403, "only the comment author can delete a comment")
    await delete_plan_comment(thread_id, comment_id)
    return {"ok": True}
