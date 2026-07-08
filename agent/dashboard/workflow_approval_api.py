"""REST API for approving workflow-file pushes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from .oauth import require_same_origin_for_mutations, require_session
from .plan_api import _dispatch_followup, _thread_metadata
from .thread_api import _thread_is_readable, _user_owns_thread
from .workflow_approval import (
    decide_workflow_push_approval,
    get_workflow_push_approvals,
    workflow_push_approval_responses,
)

workflow_approval_router = APIRouter(
    prefix="/dashboard/api/workflow-approval",
    tags=["workflow-approval"],
    dependencies=[Depends(require_same_origin_for_mutations)],
)
_SESSION_DEP = Depends(require_session)


@workflow_approval_router.get("/{thread_id}")
async def list_workflow_push_approvals(
    thread_id: str, session: dict[str, Any] = _SESSION_DEP
) -> dict[str, Any]:
    metadata = await _thread_metadata(thread_id)
    if not _thread_is_readable(metadata):
        raise HTTPException(404, "thread not found")
    is_owner = _user_owns_thread(metadata, session["sub"], session.get("email"))
    if not is_owner:
        raise HTTPException(403, "only the thread owner can view workflow approvals")
    approvals = await get_workflow_push_approvals(thread_id)
    return {
        "threadId": thread_id,
        "isOwner": is_owner,
        "approvals": workflow_push_approval_responses(approvals),
    }


@workflow_approval_router.post("/{thread_id}/{fingerprint}/approve")
async def approve_workflow_push(
    thread_id: str, fingerprint: str, session: dict[str, Any] = _SESSION_DEP
) -> dict[str, Any]:
    metadata = await _thread_metadata(thread_id)
    if not _user_owns_thread(metadata, session["sub"], session.get("email")):
        raise HTTPException(403, "only the thread owner can approve workflow pushes")
    record = await decide_workflow_push_approval(
        thread_id, fingerprint, approved=True, actor=session["sub"]
    )
    if record is None:
        raise HTTPException(404, "workflow push approval not found")
    await _dispatch_followup(
        thread_id,
        metadata,
        "The workflow-file push approval was approved. Retry the blocked git push now; do not alter workflow files before pushing.",
        plan_mode=False,
    )
    return {"status": "approved", "fingerprint": fingerprint}


@workflow_approval_router.post("/{thread_id}/{fingerprint}/reject")
async def reject_workflow_push(
    thread_id: str, fingerprint: str, session: dict[str, Any] = _SESSION_DEP
) -> dict[str, Any]:
    metadata = await _thread_metadata(thread_id)
    if not _user_owns_thread(metadata, session["sub"], session.get("email")):
        raise HTTPException(403, "only the thread owner can reject workflow pushes")
    record = await decide_workflow_push_approval(
        thread_id, fingerprint, approved=False, actor=session["sub"]
    )
    if record is None:
        raise HTTPException(404, "workflow push approval not found")
    return {"status": "rejected", "fingerprint": fingerprint}
