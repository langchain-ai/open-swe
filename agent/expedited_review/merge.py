"""Turn an expedited review's recorded approvals into GitHub reviews and a merge.

The agent calls this once it believes the pull request is ready. Votes count
for the current head only while the diff the card drew is unchanged; GitHub's
answer to the merge is final and there is no admin bypass.
"""

import logging
from dataclasses import dataclass
from typing import Any, Literal

import httpx2

from agent.dashboard.profiles import get_valid_access_token
from agent.expedited_review.approvals import REQUIRED_APPROVALS, ExpeditedApproval
from agent.expedited_review.eligibility import diff_fingerprint, fetch_changed_files
from agent.expedited_review.lifecycle import mark_merged, repo_token, retire
from agent.expedited_review.readiness import assess_readiness
from agent.expedited_review.voting import github_token_hint, is_author
from agent.github.app import (
    get_github_app_installation_id_for_repo,
    get_github_app_installation_token,
)
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


def _github_error(response: httpx2.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    message = body.get("message") if isinstance(body, dict) else None
    text = message if isinstance(message, str) and message else response.text[:200]
    return f"{response.status_code} {text}".strip()


async def _submit_github_approval(
    approval: ExpeditedApproval, login: str, head_sha: str
) -> int | str:
    """POST an ``APPROVE`` review as ``login``; the review id, or why it failed."""
    user_token = await get_valid_access_token(login)
    if not user_token:
        return f"Open SWE has no GitHub token for @{login}. {github_token_hint()}"
    pr = approval.pull_request
    url = f"{GITHUB_API_BASE}/repos/{pr.owner}/{pr.repo}/pulls/{pr.number}/reviews"
    payload = {
        "commit_id": head_sha,
        "event": "APPROVE",
        "body": "Approved via Open SWE expedited review in Slack.",
    }
    try:
        async with github_client(token=user_token) as client:
            response = await github_request(client, "POST", url, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx2.HTTPStatusError as exc:
        return f"GitHub rejected @{login}'s review: {_github_error(exc.response)}"
    except httpx2.HTTPError, ValueError:
        return f"GitHub did not answer when submitting @{login}'s review."
    review_id = data.get("id") if isinstance(data, dict) else None
    if not isinstance(review_id, int):
        return f"GitHub returned an unexpected review response for @{login}."
    return review_id


async def _record_review(
    approval: ExpeditedApproval, login: str, review_id: int, head_sha: str
) -> None:
    async with ExpeditedApproval.locked(approval.id) as (_, row):
        if row is None:
            return
        for vote in row.votes:
            if vote.github_login == login:
                vote.github_review_id = review_id
                vote.github_review_sha = head_sha


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
        await retire(approval, "cancelled", "The pull request was closed.")
        return MergeResult("closed", "The pull request is closed; the expedited review ended.")

    files = await fetch_changed_files(
        owner=pr.owner, repo=pr.repo, pr_number=pr.number, token=token
    )
    if files is None:
        return MergeResult("error", "Could not read the pull request's changed files.")
    if diff_fingerprint(files) != approval.diff_fingerprint:
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

    approvals = approval.approvals
    if len(approvals) < REQUIRED_APPROVALS:
        remaining = REQUIRED_APPROVALS - len(approvals)
        return MergeResult(
            "needs_approvals",
            f"{remaining} more approval{'s' if remaining != 1 else ''} needed on the Slack "
            "card. You will be woken when the card has enough.",
        )
    if readiness.blockers:
        return MergeResult("not_ready", "Not ready to merge: " + "; ".join(readiness.blockers))

    for vote in approvals:
        login = vote.github_login
        if is_author(approval, vote.voter_user_id, login):
            continue
        if vote.github_review_id is not None and vote.github_review_sha == snapshot.head_sha:
            continue
        submitted = await _submit_github_approval(approval, login, snapshot.head_sha)
        if isinstance(submitted, str):
            return MergeResult("error", submitted)
        await _record_review(approval, login, submitted, snapshot.head_sha)

    merge_token = await _merge_token(pr.owner, pr.repo) or token
    methods = snapshot.allowed_merge_methods or ["merge"]
    url = f"{GITHUB_API_BASE}/repos/{pr.owner}/{pr.repo}/pulls/{pr.number}/merge"
    payload: dict[str, Any] = {"sha": snapshot.head_sha, "merge_method": methods[0]}
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
        await mark_merged(approval)
        return MergeResult("merged", f"Merged {pr.url}.")
    return MergeResult(
        "refused",
        f"GitHub refused the merge: {_github_error(response)}. Open SWE never bypasses branch "
        "protection; ask a maintainer if the rules need someone else's approval.",
    )
