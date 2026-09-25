"""Merge a pull request on its expedited approvals.

The agent calls this once it believes the pull request is ready. Votes count
for the current head while the diff the card drew is unchanged, or when the
agent judges a change needs no re-review; GitHub's answer to the merge is final
and there is no admin bypass.
"""

import logging
from dataclasses import dataclass
from typing import Any, Literal

import httpx2

from agent.expedited_review.approvals import ExpeditedApproval
from agent.expedited_review.eligibility import (
    Ineligible,
    assess_eligibility,
    fetch_changed_files,
    fingerprint_matches,
)
from agent.expedited_review.lifecycle import mark_merged, repo_token, retire
from agent.expedited_review.readiness import assess_readiness
from agent.expedited_review.reviews import github_error, submit_approval
from agent.github.app import (
    get_github_app_installation_id_for_repo,
    get_github_app_installation_token,
)
from agent.github.ci import fetch_pr
from agent.github.comments import post_github_comment
from agent.github.http import GITHUB_API_BASE, github_client, github_request

logger = logging.getLogger(__name__)

_MERGE_PERMISSIONS = {"contents": "write", "pull_requests": "write"}

MergeStatus = Literal[
    "merged",
    "not_ready",
    "needs_approvals",
    "diff_changed",
    "invalidated",
    "closed",
    "refused",
    "error",
]


@dataclass(frozen=True, slots=True)
class MergeResult:
    status: MergeStatus
    message: str


_NO_APPROVALS = MergeResult(
    "needs_approvals",
    "Nobody has approved the Slack card yet. You will be woken when someone does.",
)


async def _merge_token(owner: str, repo: str) -> str | None:
    installation_id = await get_github_app_installation_id_for_repo(owner, repo)
    if installation_id is None:
        return None
    return await get_github_app_installation_token(
        installation_id=installation_id,
        repositories=[repo],
        permissions=_MERGE_PERMISSIONS,
    )


async def _keep_approval(
    approval: ExpeditedApproval, fingerprint: str, reason: str, token: str
) -> ExpeditedApproval | None:
    """Carry the votes over to the current diff, noting on the PR why no re-review was needed."""
    async with ExpeditedApproval.locked(approval.id) as (_, row):
        if row is None or row.state != "open":
            return None
        row.diff_fingerprint = fingerprint
    pr = approval.pull_request
    await post_github_comment(
        {"owner": pr.owner, "name": pr.repo},
        pr.number,
        "The diff changed after the expedited approval; the approval was kept because: "
        f"{reason.strip()}",
        token=token,
    )
    logger.info(
        "Kept expedited approval across a diff change",
        extra={"approval_id": str(approval.id)},
    )
    return await ExpeditedApproval.get(approval.id)


async def merge_approved(
    approval: ExpeditedApproval, keep_approval_reason: str = ""
) -> MergeResult:
    pr = approval.pull_request
    token = await repo_token(pr.owner, pr.repo)
    if token is None:
        return MergeResult("error", "Open SWE cannot reach this repository's GitHub App.")
    readiness = await assess_readiness(
        owner=pr.owner, repo=pr.repo, pr_number=pr.number, token=token
    )
    if readiness is None:
        return MergeResult("error", "GitHub was unavailable while checking the pull request.")
    snapshot = readiness.snapshot
    if snapshot.merged:
        await mark_merged(approval)
        return MergeResult("merged", f"{pr.url} is already merged.")
    if snapshot.state != "open":
        await retire(approval, "cancelled", "the pull request was closed")
        return MergeResult("closed", "The pull request is closed; the expedited review ended.")

    files = await fetch_changed_files(
        owner=pr.owner, repo=pr.repo, pr_number=pr.number, token=token
    )
    if files is None:
        return MergeResult("error", "Could not read the pull request's changed files.")
    if not fingerprint_matches(files, approval.diff_fingerprint):
        if not keep_approval_reason.strip():
            return MergeResult(
                "diff_changed",
                "A commit since the card was posted changed the non-test diff the approver "
                "saw. Nothing was discarded. If the change does not need the approver to look "
                "again, call `merge_expedited_pr` again with `keep_approval_reason`; "
                "otherwise call `expedite_pr_approval` for a fresh card.",
            )
        verdict = assess_eligibility(files)
        if isinstance(verdict, Ineligible):
            await retire(
                approval,
                "superseded",
                "A later commit grew the diff past expedited review; votes were discarded.",
            )
            return MergeResult(
                "invalidated",
                f"The diff is no longer eligible for expedited review ({verdict.reason}), so "
                "the approval was discarded. Ask for a normal GitHub review.",
            )
        kept = await _keep_approval(approval, verdict.fingerprint, keep_approval_reason, token)
        if kept is None:
            return MergeResult(
                "closed", "The expedited review closed before the merge; nothing was merged."
            )
        approval = kept

    if approval.awaiting_ready:
        return MergeResult(
            "needs_approvals",
            "The author has not marked the draft ready on the Slack card yet. You will be "
            "woken once someone approves it.",
        )
    if not approval.approvals:
        return _NO_APPROVALS
    if readiness.blockers:
        return MergeResult("not_ready", "Not ready to merge: " + "; ".join(readiness.blockers))

    # Held through the merge so a Dismiss or second merge call waits for it.
    async with ExpeditedApproval.locked(approval.id) as (_, row):
        if row is None or row.state != "open":
            return MergeResult(
                "closed", "The expedited review closed before the merge; nothing was merged."
            )
        if not row.approvals:
            return _NO_APPROVALS
        current = await fetch_pr(owner=pr.owner, repo=pr.repo, pr_number=pr.number, token=token)
        head = current.get("head") if current else None
        if not isinstance(head, dict) or head.get("sha") != snapshot.head_sha:
            return MergeResult(
                "not_ready",
                "The pull request's head changed while it was being checked. Call "
                "`merge_expedited_pr` again.",
            )
        for vote in row.approvals:
            # An approval on an older head still counts unless GitHub dismissed it as stale;
            # one on this head was submitted by a click after the snapshot was read.
            if vote.github_review_id is not None and (
                vote.github_review_id in snapshot.approved_review_ids
                or vote.github_review_sha == snapshot.head_sha
            ):
                continue
            failed = await submit_approval(row, vote, snapshot.head_sha)
            if failed is not None:
                return MergeResult("error", failed)
        result = await _merge(row, snapshot.head_sha, snapshot.allowed_merge_methods, token)
    if result.status == "merged":
        await mark_merged(approval)
    return result


async def _merge(
    approval: ExpeditedApproval, head_sha: str, allowed_methods: list[str], token: str
) -> MergeResult:
    pr = approval.pull_request
    merge_token = await _merge_token(pr.owner, pr.repo) or token
    methods = allowed_methods or ["merge"]
    url = f"{GITHUB_API_BASE}/repos/{pr.owner}/{pr.repo}/pulls/{pr.number}/merge"
    payload: dict[str, Any] = {"sha": head_sha, "merge_method": methods[0]}
    try:
        async with github_client(token=merge_token) as client:
            response = await github_request(client, "PUT", url, json=payload)
    except httpx2.HTTPError:
        logger.warning(
            "Expedited merge request did not complete",
            extra={"approval_id": str(approval.id)},
            exc_info=True,
        )
        return MergeResult("error", "GitHub did not answer the merge request. Try again.")
    if response.status_code == 200:
        return MergeResult("merged", f"Merged {pr.url}.")
    return MergeResult(
        "refused",
        f"GitHub refused the merge: {github_error(response)}. Open SWE never bypasses branch "
        "protection; ask a maintainer if the rules need someone else's approval.",
    )
