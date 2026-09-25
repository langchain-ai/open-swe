"""Mark ready, Approve, Broadcast and Dismiss clicks on an expedited review card.

A voter is a person (``users`` row) reached through their Slack identity whose
GitHub identity has write access to the repository. Only the author may mark a
draft ready, and only someone else may approve. An approval is submitted as the
voter's GitHub review as soon as it is recorded; the agent merges later.
"""

import logging
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import HTTPException

from agent.dashboard.profiles import get_valid_access_token
from agent.expedited_review.approvals import ApprovalVote, ExpeditedApproval
from agent.expedited_review.eligibility import fetch_changed_files, fingerprint_matches
from agent.expedited_review.lifecycle import (
    broadcast_card,
    notify_agent,
    refresh_card,
    repo_token,
    retire,
)
from agent.expedited_review.reviews import (
    dismiss_approval,
    github_token_hint,
    settings_hint,
    submit_approval,
)
from agent.github.ci import fetch_pr, has_repo_write_permission
from agent.github.pull_request_actions import MarkReadyAction, act_on_pull_request
from agent.github.pull_requests import PullRequestPayload
from agent.input_messages import PersonIdentity, split_person_id
from agent.prompts import render_prompt
from agent.slack.client import post_slack_ephemeral_message, slack_thread_mutation_lock
from agent.users import User
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

VoteAction = Literal["approve", "ready"]
CardAction = VoteAction | Literal["dismiss", "broadcast"]


@dataclass(frozen=True, slots=True)
class VoteOutcome:
    message: str


def _slack_link_hint() -> str:
    """A missing Slack link is fixed by the Slack connect flow, nothing else.

    Signing in with GitHub creates the GitHub identity and no Slack one, so
    telling someone already signed in to do that again sends them in a circle.
    """
    return settings_hint("Connect Slack under Personal connections")


@dataclass(frozen=True, slots=True)
class Voter:
    user: User
    github_login: str


def _linked(user: User | None) -> Voter | VoteOutcome:
    if user is None or not any(identity.provider == "github" for identity in user.identities):
        return VoteOutcome(f"Your Slack account is not linked to GitHub. {_slack_link_hint()}")
    return Voter(user=user, github_login=user.login_for("github"))


async def _resolve_voter(approval: ExpeditedApproval, user: User | None) -> Voter | VoteOutcome:
    """The authorized voter behind a click, or why they are not one."""
    linked = _linked(user)
    if isinstance(linked, VoteOutcome):
        return linked
    login = linked.github_login
    pr = approval.pull_request
    token = await repo_token(pr.owner, pr.repo)
    if token is None:
        return VoteOutcome("Open SWE cannot reach this repository's GitHub App installation.")
    if not await has_repo_write_permission(
        owner=pr.owner, repo=pr.repo, username=login, token=token
    ):
        return VoteOutcome(f"@{login} does not have write access to {pr.owner}/{pr.repo}.")
    return linked


async def handle_vote(
    approval: ExpeditedApproval,
    *,
    decision: VoteAction,
    user: User | None,
    broadcast: bool = False,
) -> VoteOutcome:
    """Record one click. Slow work runs unlocked; the row lock covers only the write."""
    if approval.state != "open":
        return VoteOutcome("This expedited review is no longer accepting votes.")
    if decision == "ready":
        # The author's own token decides; a fork's author has no write access upstream.
        author = _linked(user)
        if isinstance(author, VoteOutcome):
            return author
        if not approval.is_author(author.user.id, author.github_login):
            return VoteOutcome("Only the pull request's author can mark it ready for review.")
        return await _mark_ready(approval, voter=author, broadcast=broadcast)
    voter = await _resolve_voter(approval, user)
    if isinstance(voter, VoteOutcome):
        return voter
    authored = approval.is_author(voter.user.id, voter.github_login)

    if approval.awaiting_ready:
        return VoteOutcome("The author has to mark this draft ready for review first.")
    if authored:
        return VoteOutcome("You authored this pull request; someone else has to approve it.")
    if approval.vote_by(voter.user.id) is not None:
        return VoteOutcome("You already approved this revision.")
    if not await get_valid_access_token(voter.github_login):
        return VoteOutcome(
            f"Open SWE has no GitHub token for @{voter.github_login}, so it cannot submit "
            f"your review. {github_token_hint()}"
        )

    async with ExpeditedApproval.locked(approval.id) as (_, row):
        if row is None or row.state != "open":
            return VoteOutcome("This expedited review closed before your vote was recorded.")
        first_approval = not row.approved
        added = row.vote_by(voter.user.id) is None
        if added:
            row.votes.append(ApprovalVote(voter_user_id=voter.user.id, decision="approve"))
    current = await ExpeditedApproval.get(approval.id)
    if current is None:
        return VoteOutcome("This expedited review vanished.")
    problem = await _submit_review(current, voter.user.id) if added else None
    await refresh_card(current)
    recorded = (
        f"Approval recorded as @{voter.github_login}. {problem}"
        if problem
        else f"Approved on GitHub as @{voter.github_login}."
    )
    if first_approval and not await notify_agent(
        current,
        render_prompt(
            "runs/expedited-review-approved.md",
            pr_url=current.pull_request.url,
            approvers=", ".join(f"@{login}" for login in current.approvers),
        ),
    ):
        return VoteOutcome(
            f"{recorded} Open SWE could not be woken to merge it; tag it in the thread to "
            "try again."
        )
    return VoteOutcome(recorded)


async def _submit_review(approval: ExpeditedApproval, voter_user_id: UUID) -> str | None:
    """Send a new vote to GitHub now; what kept it off GitHub, or ``None`` once it is there.

    A vote that could not be sent stays recorded, and the merge submits it.
    """
    vote = approval.vote_by(voter_user_id)
    if vote is None:
        return "This expedited review vanished."
    pr = approval.pull_request
    unavailable = "GitHub was unavailable, so Open SWE will submit your review when it merges."
    token = await repo_token(pr.owner, pr.repo)
    if token is None:
        return unavailable
    payload = await fetch_pr(owner=pr.owner, repo=pr.repo, pr_number=pr.number, token=token)
    files = await fetch_changed_files(
        owner=pr.owner, repo=pr.repo, pr_number=pr.number, token=token
    )
    head_sha = PullRequestPayload.model_validate(payload).head_sha if payload else ""
    if not head_sha or files is None:
        return unavailable
    if not fingerprint_matches(files, approval.diff_fingerprint):
        return "A later commit changed the diff on this card, so no review was submitted."
    failed = await submit_approval(approval, vote, head_sha)
    if failed is not None:
        return f"{failed} Open SWE will try again when it merges."
    async with ExpeditedApproval.locked(approval.id) as (_, row):
        stored = row.vote_by(voter_user_id) if row is not None and row.state == "open" else None
        if stored is not None:
            stored.github_review_id = vote.github_review_id
            stored.github_review_sha = vote.github_review_sha
    if stored is None:
        await dismiss_approval(approval, vote, token, "the card closed before this review landed")
        return "The card closed while GitHub was answering, so the review was withdrawn."
    return None


async def _mark_ready(approval: ExpeditedApproval, *, voter: Voter, broadcast: bool) -> VoteOutcome:
    """Undraft the PR as its author, then open the card for approval."""
    if not approval.awaiting_ready:
        return VoteOutcome("This pull request is already ready for review.")
    token = await get_valid_access_token(voter.github_login)
    if not token:
        return VoteOutcome(
            f"Open SWE has no GitHub token for @{voter.github_login}, so it cannot mark the "
            f"pull request ready. {github_token_hint()}"
        )
    pr = approval.pull_request
    try:
        await act_on_pull_request(
            pr.owner, pr.repo, pr.number, MarkReadyAction(action="mark-ready"), token
        )
    except HTTPException as exc:
        return VoteOutcome(f"GitHub did not mark the pull request ready: {exc.detail}")
    async with ExpeditedApproval.locked(approval.id) as (_, row):
        if row is None or row.state != "open":
            return VoteOutcome("This expedited review closed before it was marked ready.")
        row.awaiting_ready = False
    marked = "Marked ready for review. Someone else can approve it now."
    current = await ExpeditedApproval.get(approval.id)
    if current is None:
        return VoteOutcome(marked)
    if broadcast and await broadcast_card(current):
        return VoteOutcome(f"{marked} Sent to the channel too.")
    await refresh_card(current)
    if broadcast:
        return VoteOutcome(f"{marked} It could not be sent to the channel.")
    return VoteOutcome(marked)


async def request_broadcast(approval: ExpeditedApproval) -> VoteOutcome:
    """Anyone in the thread may send an open card to the channel."""
    if approval.state != "open" or approval.approved:
        return VoteOutcome("This expedited review is no longer waiting for approval.")
    if approval.slack_broadcast:
        return VoteOutcome("This expedited review is already in the channel.")
    if not await broadcast_card(approval):
        return VoteOutcome("Open SWE could not send this expedited review to the channel.")
    return VoteOutcome("Sent to the channel.")


async def dismiss(approval: ExpeditedApproval, slack_user_id: str) -> VoteOutcome:
    """Anyone in the thread may take the card down; it needs no GitHub link or access."""
    if await retire(approval, "cancelled", f"dismissed by <@{slack_user_id}>") is None:
        return VoteOutcome("This expedited review is already closed.")
    return VoteOutcome("Dismissed.")


async def process_vote(
    approval_id: str,
    *,
    decision: CardAction,
    person: PersonIdentity,
    channel_id: str,
    thread_ts: str,
    broadcast_requested: bool = False,
) -> None:
    """Background entry point for a Slack click; answers the clicker ephemerally."""
    slack_user_id = split_person_id(person)[1]
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
            match decision:
                case "dismiss":
                    outcome = await dismiss(approval, slack_user_id)
                case "broadcast":
                    outcome = await request_broadcast(approval)
                case _:
                    outcome = await handle_vote(
                        approval,
                        decision=decision,
                        user=await User.for_person(person),
                        broadcast=broadcast_requested,
                    )
    except Exception:
        logger.exception("Expedited review vote failed", extra={"approval_id": approval_id})
        outcome = VoteOutcome("Something went wrong recording your vote. Try again.")
    await post_slack_ephemeral_message(channel_id, slack_user_id, outcome.message, thread_ts)
