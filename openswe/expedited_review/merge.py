"""Merge a pull request on its expedited approvals.

The agent calls this once it believes the pull request is ready. Votes count
for the current head while the diff the card drew is unchanged, or when the
agent judges a change needs no re-review; GitHub's answer to the merge is final
and there is no admin bypass.
"""

import logging

from openswe.expedited_review.eligibility import (
    Ineligible,
    assess_eligibility,
    fetch_changed_files,
    fingerprint_matches,
)
from openswe.expedited_review.readiness import assess_readiness
from openswe.expedited_review.reviews import submit_approval
from openswe.github.ci import fetch_pr
from openswe.github.comments import post_github_comment
from openswe.github.http import GitHubClient
from openswe.human_review.lifecycle import mark_merged, retire
from openswe.human_review.merging import MergeResult, merge_pull_request
from openswe.human_review.people import repo_token
from openswe.human_review.requests import HumanReviewRequest

logger = logging.getLogger(__name__)

_NO_APPROVALS = MergeResult(
    "needs_approvals",
    "Nobody has approved the Slack card yet. You will be woken when someone does.",
)


async def _keep_approval(
    approval: HumanReviewRequest, fingerprint: str, reason: str, token: str
) -> HumanReviewRequest | None:
    """Carry the votes over to the current diff once the PR says why no re-review was needed."""
    pr = approval.pull_request
    async with HumanReviewRequest.locked(approval.id) as (_, row):
        if row is None or row.state != "open":
            return None
        if not await post_github_comment(
            {"owner": pr.owner, "name": pr.repo},
            pr.number,
            "The diff changed after the expedited approval; the approval was kept because: "
            f"{reason.strip()}",
            token=token,
        ):
            return None
        row.diff_fingerprint = fingerprint
    logger.info(
        "Kept expedited approval across a diff change",
        extra={"approval_id": str(approval.id)},
    )
    return await HumanReviewRequest.get(approval.id)


async def merge_approved(
    approval: HumanReviewRequest, keep_approval_reason: str = ""
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
        if not keep_approval_reason.strip():
            return MergeResult(
                "diff_changed",
                "A commit since the card was posted changed the non-test diff the approver "
                "saw. Nothing was discarded. If the change does not need the approver to look "
                "again, call `merge_expedited_pr` again with `keep_approval_reason`; "
                "otherwise call `expedite_pr_approval` for a fresh card.",
            )
        kept = await _keep_approval(approval, verdict.fingerprint, keep_approval_reason, token)
        if kept is None:
            return MergeResult(
                "error",
                "Could not record why the approval was kept, so nothing was merged. Try again.",
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
    async with HumanReviewRequest.locked(approval.id) as (_, row):
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
        async with GitHubClient.connect(token=token) as github:
            threads = (
                await github.repo(pr.owner, pr.repo).pull_request(pr.number).unresolved_threads()
            )
        if threads is None:
            return MergeResult("error", "GitHub was unavailable while checking review threads.")
        if threads:
            return MergeResult(
                "not_ready", f"Not ready to merge: {len(threads)} unresolved review threads"
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
        result = await merge_pull_request(
            row, snapshot.head_sha, snapshot.allowed_merge_methods, token
        )
    if result.status == "merged":
        await mark_merged(approval)
    return result
