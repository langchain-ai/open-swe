"""Tool that merges a pull request on the approvals its expedited review card collected."""

from typing import Any

from langgraph.config import get_config

from agent.dashboard.workspace_settings import get_workspace_settings
from agent.expedited_review.approvals import ExpeditedApproval
from agent.expedited_review.merge import merge_approved
from agent.run_config import RunConfig
from agent.slack.client import parse_github_pr_url


async def merge_expedited_pr(pr_url: str, keep_approval_reason: str = "") -> dict[str, Any]:
    """Implement the `merge_expedited_pr` tool."""
    pr_ref = parse_github_pr_url(pr_url)
    if pr_ref is None:
        return {"success": False, "error": "pr_url must be a canonical GitHub pull request URL"}
    thread_id = RunConfig.from_config(get_config()).thread_id
    if not thread_id:
        return {"success": False, "error": "No executable agent thread is available"}
    if not (await get_workspace_settings()).expedited_review_enabled:
        return {"success": False, "error": "Expedited review is disabled for this instance."}
    approval = await ExpeditedApproval.active_for(pr_ref.owner, pr_ref.repo, pr_ref.number)
    if approval is None:
        return {
            "success": False,
            "status": "no_card",
            "error": "This pull request has no open expedited review card. Call "
            "`expedite_pr_approval` to post one, or ask for a normal GitHub review.",
        }
    if approval.thread_id and approval.thread_id != thread_id:
        return {"success": False, "error": "This expedited review belongs to another agent thread"}
    result = await merge_approved(approval, keep_approval_reason)
    return {
        "success": result.status == "merged",
        "status": result.status,
        "message": result.message,
    }
