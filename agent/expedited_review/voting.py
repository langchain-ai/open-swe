"""Approve and Reject clicks, the GitHub reviews they become, and the merge.

A voter is a person (``users`` row) reached through their Slack identity whose
GitHub identity has write access to the repository. A non-author approval is
submitted to GitHub as that person's own ``APPROVE`` review before it counts. The second distinct approval merges the
pull request with the GitHub App's token, conditional on the reviewed head SHA.
GitHub's answer is final: there is no admin bypass.
"""

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx2

from agent.dashboard.profiles import get_valid_access_token
from agent.expedited_review.approvals import (
    REQUIRED_APPROVALS,
    ApprovalVote,
    ExpeditedApproval,
    VoteDecision,
)
from agent.expedited_review.readiness import Readiness, assess_readiness
from agent.expedited_review.watch import (
    mark_merged,
    refresh_card,
    repo_token,
    retire,
    transition,
)
from agent.github.app import (
    get_github_app_installation_id_for_repo,
    get_github_app_installation_token,
)
from agent.github.ci import has_repo_write_permission
from agent.github.http import GITHUB_API_BASE, github_client, github_request
from agent.input_messages import PersonIdentity
from agent.prompts import render_prompt
from agent.slack.client import (
    post_slack_ephemeral_message,
    post_slack_thread_reply,
    slack_thread_mutation_lock,
)
from agent.users import User
from agent.users.resolve import resolve_person, split_identity
from agent.utils.dashboard_links import dashboard_base_url
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

_MERGE_PERMISSIONS = {"contents": "write", "pull_requests": "write"}


@dataclass(frozen=True, slots=True)
class VoteOutcome:
    message: str
    private: bool = True


def _reconnect_hint() -> str:
    base = dashboard_base_url()
    if base:
        return f"Sign in to the Open SWE dashboard with GitHub first: {base}"
    return "Sign in to the Open SWE dashboard with GitHub first."


@dataclass(frozen=True, slots=True)
class Voter:
    user: User
    github_login: str
    app_token: str


async def _resolve_voter(approval: ExpeditedApproval, user: User | None) -> Voter | VoteOutcome:
    """The authorized voter behind a click, or why they are not one."""
    login = user.login_for("github") if user is not None else ""
    if user is None or not any(identity.provider == "github" for identity in user.identities):
        return VoteOutcome(f"Your Slack account is not linked to GitHub. {_reconnect_hint()}")
    pr = approval.pull_request
    token = await repo_token(pr.owner, pr.repo)
    if token is None:
        return VoteOutcome("Open SWE cannot reach this repository's GitHub App installation.")
    if not await has_repo_write_permission(
        owner=pr.owner, repo=pr.repo, username=login, token=token
    ):
        return VoteOutcome(f"@{login} does not have write access to {pr.owner}/{pr.repo}.")
    return Voter(user=user, github_login=login, app_token=token)


def _is_author(approval: ExpeditedApproval, voter: Voter) -> bool:
    pr = approval.pull_request
    if pr.author_user_id is not None:
        return pr.author_user_id == voter.user.id
    return bool(pr.author) and pr.author.lower() == voter.github_login.lower()


async def _submit_github_approval(approval: ExpeditedApproval, login: str) -> int | VoteOutcome:
    """POST an ``APPROVE`` review as ``login``; the review id, or why it failed."""
    user_token = await get_valid_access_token(login)
    if not user_token:
        return VoteOutcome(
            f"Open SWE has no GitHub token for @{login}, so it cannot submit your review. "
            f"{_reconnect_hint()}"
        )
    pr = approval.pull_request
    url = f"{GITHUB_API_BASE}/repos/{pr.owner}/{pr.repo}/pulls/{pr.number}/reviews"
    payload = {
        "commit_id": approval.head_sha,
        "event": "APPROVE",
        "body": "Approved via Open SWE expedited review in Slack.",
    }
    try:
        async with github_client(token=user_token) as client:
            response = await github_request(client, "POST", url, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx2.HTTPStatusError as exc:
        detail = _github_error(exc.response)
        return VoteOutcome(f"GitHub rejected your review: {detail}")
    except httpx2.HTTPError, ValueError:
        return VoteOutcome("GitHub did not answer when submitting your review. Try again.")
    review_id = data.get("id") if isinstance(data, dict) else None
    if not isinstance(review_id, int):
        return VoteOutcome("GitHub returned an unexpected review response. Try again.")
    return review_id


def _github_error(response: httpx2.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    message = body.get("message") if isinstance(body, dict) else None
    text = message if isinstance(message, str) and message else response.text[:200]
    return f"{response.status_code} {text}".strip()


async def handle_vote(
    approval: ExpeditedApproval,
    *,
    decision: VoteDecision,
    user: User | None,
    feedback: str = "",
) -> VoteOutcome:
    """Record one click. Slow work runs unlocked; the row lock covers only the write."""
    if approval.state != "open":
        return VoteOutcome("This expedited review is no longer accepting votes.")
    voter = await _resolve_voter(approval, user)
    if isinstance(voter, VoteOutcome):
        return voter
    login, app_token = voter.github_login, voter.app_token
    pr = approval.pull_request

    if decision == "reject":
        return await _reject(approval, voter=voter, feedback=feedback)

    if approval.vote_by(voter.user.id) is not None:
        return VoteOutcome("You already approved this revision.")
    review_id: int | None = None
    if not _is_author(approval, voter):
        submitted = await _submit_github_approval(approval, login)
        if isinstance(submitted, VoteOutcome):
            return submitted
        review_id = submitted

    async with ExpeditedApproval.locked(approval.id) as (_, row):
        if row is None or row.state != "open" or row.head_sha != approval.head_sha:
            return VoteOutcome("This expedited review closed before your vote was recorded.")
        if row.vote_by(voter.user.id) is None:
            row.votes.append(
                ApprovalVote(
                    voter_user_id=voter.user.id, decision="approve", github_review_id=review_id
                )
            )
        quorum = len(row.approvals) >= REQUIRED_APPROVALS
        if quorum:
            row.state = "merging"
    current = await ExpeditedApproval.get(approval.id)
    if current is None:
        return VoteOutcome("This expedited review vanished.")
    if not quorum:
        await refresh_card(current)
        return VoteOutcome(
            f"Approval recorded as @{login}."
            + (" A GitHub review was submitted." if review_id else "")
        )
    readiness = await assess_readiness(
        owner=pr.owner, repo=pr.repo, pr_number=pr.number, token=app_token
    )
    if readiness is None:
        return VoteOutcome("Approval recorded. GitHub is unreachable; the merge will be retried.")
    status = await complete_merge(current, readiness, app_token)
    if status == "merged":
        return VoteOutcome(f"Merged {pr.url}.", private=False)
    return VoteOutcome("Approval recorded; see the card for the merge outcome.")


async def _reject(approval: ExpeditedApproval, *, voter: Voter, feedback: str) -> VoteOutcome:
    clean_feedback = " ".join(feedback.split())[:3000]
    login = voter.github_login
    async with ExpeditedApproval.locked(approval.id) as (_, row):
        if row is None or row.state != "open":
            return VoteOutcome("This expedited review is no longer accepting votes.")
        row.votes = [vote for vote in row.votes if vote.voter_user_id != voter.user.id]
        row.votes.append(
            ApprovalVote(voter_user_id=voter.user.id, decision="reject", feedback=clean_feedback)
        )
    outcome = f"Rejected by @{login}." + (f" Feedback: {clean_feedback}" if clean_feedback else "")
    pr = approval.pull_request
    await retire(
        approval,
        "rejected",
        outcome,
        expected=("open",),
        agent_prompt=render_prompt(
            "runs/expedited-review-rejected.md",
            pr_url=pr.url,
            rejector=login,
            feedback=clean_feedback or "(none given)",
        ),
    )
    return VoteOutcome("Rejected. The agent has been told.")


async def _merge_token(owner: str, repo: str) -> str | None:
    installation_id = await get_github_app_installation_id_for_repo(owner, repo)
    if installation_id is None:
        return None
    return await get_github_app_installation_token(
        installation_id=installation_id,
        repositories=[repo],
        permissions=_MERGE_PERMISSIONS,
    )


async def complete_merge(approval: ExpeditedApproval, readiness: Readiness, token: str) -> str:
    """Revalidate and merge a ``merging`` approval; GitHub's refusal ends the vote."""
    pr = approval.pull_request
    snapshot = readiness.snapshot
    if snapshot.merged:
        await mark_merged(approval)
        return "merged"
    if snapshot.head_sha != approval.head_sha:
        await retire(approval, "superseded", "A new commit replaced the reviewed revision.")
        return "superseded"
    if not readiness.ready:
        reopened = await transition(
            approval.id,
            expected=("merging",),
            state="open",
            detail="; ".join(readiness.blockers),
        )
        if reopened is not None:
            await refresh_card(reopened)
        return "open"

    merge_token = await _merge_token(pr.owner, pr.repo) or token
    methods = snapshot.allowed_merge_methods or ["merge"]
    url = f"{GITHUB_API_BASE}/repos/{pr.owner}/{pr.repo}/pulls/{pr.number}/merge"
    payload: dict[str, Any] = {"sha": approval.head_sha, "merge_method": methods[0]}
    try:
        async with github_client(token=merge_token) as client:
            response = await github_request(client, "PUT", url, json=payload)
    except httpx2.HTTPError:
        logger.warning(
            "Merge request did not complete; leaving approval in merging",
            extra={"approval_id": str(approval.id)},
            exc_info=True,
        )
        return "merging"
    if response.status_code == 200:
        await mark_merged(approval)
        return "merged"
    if response.status_code in {405, 409, 422, 403, 404}:
        detail = _github_error(response)
        await retire(
            approval,
            "failed",
            f"GitHub refused the merge: {detail}",
            expected=("merging",),
            agent_prompt=render_prompt(
                "runs/expedited-review-merge-refused.md", pr_url=pr.url, reason=detail
            ),
        )
        return "failed"
    logger.warning(
        "Unexpected merge response; leaving approval in merging",
        extra={"approval_id": str(approval.id), "status_code": response.status_code},
    )
    return "merging"


async def process_vote(
    approval_id: str,
    *,
    decision: VoteDecision,
    person: PersonIdentity,
    channel_id: str,
    thread_ts: str,
    feedback: str = "",
) -> None:
    """Background entry point for a Slack click; answers the clicker ephemerally."""
    slack_user_id = split_identity(person)[1]
    try:
        approval = await ExpeditedApproval.get(UUID(approval_id))
    except ValueError:
        approval = None
    if approval is None:
        await post_slack_ephemeral_message(
            channel_id, slack_user_id, "That expedited review no longer exists.", thread_ts
        )
        return
    try:
        async with slack_thread_mutation_lock(
            langgraph_client(), channel_id, thread_ts, purpose=f"expedited:{approval_id}"
        ):
            outcome = await handle_vote(
                approval,
                decision=decision,
                user=await resolve_person(person),
                feedback=feedback,
            )
    except Exception:
        logger.exception("Expedited review vote failed", extra={"approval_id": approval_id})
        outcome = VoteOutcome("Something went wrong recording your vote. Try again.")
    if outcome.private:
        await post_slack_ephemeral_message(channel_id, slack_user_id, outcome.message, thread_ts)
    else:
        await post_slack_thread_reply(channel_id, thread_ts, outcome.message)
