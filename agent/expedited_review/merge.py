"""Merge a pull request on its expedited approvals.

The agent calls this once it believes the pull request is ready. Votes count
for the current head only while the diff the card drew is unchanged; GitHub's
answer to the merge is final and there is no admin bypass.
"""

import logging
from dataclasses import dataclass
from typing import Any, Literal

import httpx2

from agent.expedited_review.approvals import ExpeditedApproval
from agent.expedited_review.eligibility import fetch_changed_files, fingerprint_matches
from agent.expedited_review.lifecycle import mark_merged, repo_token, retire
from agent.expedited_review.readiness import assess_readiness
from agent.expedited_review.reviews import github_error, submit_approval
from agent.github.app import (
    get_github_app_installation_id_for_repo,
    get_github_app_installation_token,
)
from agent.github.ci import fetch_pr
from agent.github.http import GITHUB_API_BASE, github_client, github_request

logger = logging.getLogger(__name__)

_MERGE_PERMISSIONS = {"contents": "write", "pull_requests": "write"}

MergeStatus = Literal[
    "merged", "not_ready", "needs_approvals", "invalidated", "closed", "refused", "error"
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


async def merge_approved(approval: ExpeditedApproval) -> MergeResult:
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
        await retire(
            approval,
            "superseded",
            "A later commit changed the diff shown here; votes were discarded.",
        )
        return MergeResult(
            "invalidated",
            "A commit since the card was posted changed the diff voters saw, so their votes "
            "no longer count. Call `expedite_pr_approval` again for a fresh card.",
        )

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
