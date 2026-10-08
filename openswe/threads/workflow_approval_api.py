"""REST API for approving workflow-file pushes."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from langgraph_sdk.schema import Run

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.oauth import require_same_origin_for_mutations, require_session
from openswe.dispatch import dispatch_agent_run
from openswe.source_context import SourceContext
from openswe.threads.plan_api import fetch_thread_metadata
from openswe.threads.summary import (
    _assert_thread_promptable,
    repo_config_from_metadata,
    thread_is_readable,
    thread_source,
)
from openswe.threads.workflow_approval import (
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
    metadata = await fetch_thread_metadata(thread_id)
    if not thread_is_readable(metadata, session["sub"], session.get("email")):
        raise HTTPException(404, "thread not found")
    approvals = await get_workflow_push_approvals(thread_id)
    return {
        "threadId": thread_id,
        "approvals": workflow_push_approval_responses(approvals),
    }


@workflow_approval_router.post("/{thread_id}/{fingerprint}/approve")
@audit_endpoint
async def approve_workflow_push(
    thread_id: str, fingerprint: str, session: dict[str, Any] = _SESSION_DEP
) -> dict[str, Any]:
    metadata = await fetch_thread_metadata(thread_id)
    _assert_thread_promptable(metadata, session["sub"])
    record = await decide_workflow_push_approval(
        thread_id, fingerprint, approved=True, actor=session["sub"]
    )
    if record is None:
        raise HTTPException(404, "workflow push approval not found")
    await dispatch_followup(
        thread_id,
        metadata,
        "The workflow-file push approval was approved. Retry the blocked git push now; do not alter workflow files before pushing.",
        github_login=session["sub"],
        user_email=session.get("email"),
    )
    return {"status": "approved", "fingerprint": fingerprint}


@workflow_approval_router.post("/{thread_id}/{fingerprint}/reject")
@audit_endpoint
async def reject_workflow_push(
    thread_id: str, fingerprint: str, session: dict[str, Any] = _SESSION_DEP
) -> dict[str, Any]:
    metadata = await fetch_thread_metadata(thread_id)
    _assert_thread_promptable(metadata, session["sub"])
    record = await decide_workflow_push_approval(
        thread_id, fingerprint, approved=False, actor=session["sub"]
    )
    if record is None:
        raise HTTPException(404, "workflow push approval not found")
    return {"status": "rejected", "fingerprint": fingerprint}


async def dispatch_followup(
    thread_id: str,
    metadata: dict[str, Any],
    text: str,
    *,
    github_login: str | None,
    user_email: str | None = None,
    multitask_strategy: str = "interrupt",
) -> Run:
    """Continue the existing thread with the decision as a new instruction run."""
    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "source": thread_source(metadata) or "slack",
        "github_login": github_login,
        "user_email": user_email,
    }
    repo = repo_config_from_metadata(metadata)
    if repo:
        configurable["repo"] = repo
    context = SourceContext.from_metadata(metadata)
    if context.slack_thread is not None:
        configurable["slack_thread"] = context.dump()["slack_thread"]

    return await dispatch_agent_run(
        thread_id,
        text,
        configurable,
        source=configurable["source"],
        thread_title=None,
        multitask_strategy=multitask_strategy,
    )
