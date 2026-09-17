"""Tool that nominates a tiny pull request for approval from its Slack thread."""

from collections.abc import Mapping
from typing import Any, Literal

from langgraph.config import get_config
from langgraph_sdk import get_client

from agent.dashboard.workspace_settings import get_workspace_settings
from agent.expedited_review.approvals import ExpeditedApproval
from agent.expedited_review.eligibility import (
    MAX_CHANGED_LINES,
    Ineligible,
    assess_eligibility,
    fetch_changed_files,
)
from agent.expedited_review.watch import evaluate_approval, retire, start_approval
from agent.github.ci import fetch_pr
from agent.github.pull_requests import PullRequest
from agent.github.token import resolve_github_token
from agent.run_config import RunConfig
from agent.slack.client import get_active_slack_thread, parse_github_pr_url
from agent.tools.manage_baby_sit import dispatch_run_config


def _failure(error: str) -> dict[str, Any]:
    return {"success": False, "error": error}


async def expedite_pr_approval(
    pr_url: str, action: Literal["start", "cancel"] = "start"
) -> dict[str, Any]:
    """Implement the `expedite_pr_approval` tool."""
    pr_ref = parse_github_pr_url(pr_url)
    if pr_ref is None:
        return _failure("pr_url must be a canonical GitHub pull request URL")
    config = get_config()
    cfg = RunConfig.from_config(config)
    thread_id = cfg.thread_id
    if not thread_id:
        return _failure("No executable agent thread is available")
    if not (await get_workspace_settings()).expedited_review_enabled:
        return _failure(
            "Expedited review is disabled for this Open SWE instance. "
            "An admin can enable it under Settings; ask for a normal review instead."
        )

    if action == "cancel":
        approval = await ExpeditedApproval.active_for(pr_ref.owner, pr_ref.repo, pr_ref.number)
        if approval is None:
            return {"success": True, "cancelled": False}
        if approval.thread_id and approval.thread_id != thread_id:
            return _failure("This expedited review belongs to another agent thread")
        await retire(approval, "failed", "Cancelled by the agent.")
        return {"success": True, "cancelled": True}

    slack_thread = await get_active_slack_thread(
        get_client(), thread_id, cfg.slack_thread.dump() if cfg.slack_thread else None
    )
    channel_id = str((slack_thread or {}).get("channel_id") or "")
    thread_ts = str((slack_thread or {}).get("thread_ts") or "")
    if not channel_id or not thread_ts:
        return _failure("Expedited review needs a Slack thread to post the approval card in")

    try:
        token, _ = await resolve_github_token(
            config if isinstance(config, Mapping) else {}, thread_id
        )
    except Exception as exc:
        return _failure(f"GitHub authentication failed: {exc}")
    pr = await fetch_pr(owner=pr_ref.owner, repo=pr_ref.repo, pr_number=pr_ref.number, token=token)
    if not pr:
        return _failure("Pull request is unavailable")
    if pr.get("state") != "open":
        return _failure("Pull request is not open")
    if pr.get("draft") is True:
        return _failure("Pull request is a draft; mark it ready for review first")
    head = pr.get("head") if isinstance(pr.get("head"), Mapping) else {}
    head_sha = head.get("sha") if isinstance(head, Mapping) else None
    if not isinstance(head_sha, str) or not head_sha:
        return _failure("Pull request head SHA is unavailable")

    files = await fetch_changed_files(
        owner=pr_ref.owner, repo=pr_ref.repo, pr_number=pr_ref.number, token=token
    )
    if files is None:
        return _failure("Could not read the pull request's changed files")
    verdict = assess_eligibility(files)
    if isinstance(verdict, Ineligible):
        return _failure(
            f"Not eligible for expedited review: {verdict.reason}. "
            f"Eligible changes touch at most {MAX_CHANGED_LINES} lines and every changed "
            "file has to have a readable text diff. Ask for a normal review."
        )

    pull_request = await PullRequest.load(pr_ref.owner, pr_ref.repo, pr_ref.number)
    pull_request = await pull_request.link_thread(thread_id, source="expedited_review")
    approval = await start_approval(
        pull_request=pull_request,
        thread_id=thread_id,
        head_sha=head_sha,
        diff_fingerprint=verdict.fingerprint,
        slack_channel_id=channel_id,
        slack_thread_ts=thread_ts,
        run_config=dispatch_run_config(cfg, thread_id, None),
    )
    status = await evaluate_approval(str(approval.id))
    return {
        "success": True,
        "approval_id": str(approval.id),
        "pr_url": pr_ref.url,
        "head_sha": head_sha,
        "changed_lines": verdict.changed_lines,
        "status": status,
        "next": (
            "The approval card is posted in the Slack thread; two approvals merge the PR."
            if status == "open"
            else "Open SWE is watching the PR and will post the card once checks and reviews "
            "are clean. Do not poll; you will be told if it is rejected or withdrawn."
        ),
    }
