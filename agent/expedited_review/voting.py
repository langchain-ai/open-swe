"""Mark ready, Approve and Reject clicks on an expedited review card.

A voter is a person (``users`` row) reached through their Slack identity whose
GitHub identity has write access to the repository. Only the author may mark a
draft ready, and only someone else may approve. An approval is only recorded;
the agent turns it into a GitHub review when it merges.
"""

import logging
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import HTTPException

from agent.dashboard.profiles import get_valid_access_token
from agent.expedited_review.approvals import ApprovalVote, ExpeditedApproval
from agent.expedited_review.lifecycle import notify_agent, refresh_card, repo_token, retire
from agent.github.ci import has_repo_write_permission
from agent.github.pull_request_actions import MarkReadyAction, act_on_pull_request
from agent.input_messages import PersonIdentity, split_person_id
from agent.prompts import render_prompt
from agent.slack.client import post_slack_ephemeral_message, slack_thread_mutation_lock
from agent.users import User
from agent.utils.dashboard_links import dashboard_base_url
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

CardAction = Literal["approve", "reject", "ready"]


@dataclass(frozen=True, slots=True)
class VoteOutcome:
    message: str


def _settings_hint(action: str) -> str:
    base = dashboard_base_url()
    return f"{action}: {base}/my-settings" if base else f"{action} in your Open SWE settings."


def _slack_link_hint() -> str:
    """A missing Slack link is fixed by the Slack connect flow, nothing else.

    Signing in with GitHub creates the GitHub identity and no Slack one, so
    telling someone already signed in to do that again sends them in a circle.
    """
    return _settings_hint("Connect Slack under Personal connections")


def github_token_hint() -> str:
    return _settings_hint("Sign in again with GitHub to refresh Open SWE's access")


@dataclass(frozen=True, slots=True)
class Voter:
    user: User
    github_login: str


async def _resolve_voter(approval: ExpeditedApproval, user: User | None) -> Voter | VoteOutcome:
    """The authorized voter behind a click, or why they are not one."""
    login = user.login_for("github") if user is not None else ""
    if user is None or not any(identity.provider == "github" for identity in user.identities):
        return VoteOutcome(f"Your Slack account is not linked to GitHub. {_slack_link_hint()}")
    pr = approval.pull_request
    token = await repo_token(pr.owner, pr.repo)
    if token is None:
        return VoteOutcome("Open SWE cannot reach this repository's GitHub App installation.")
    if not await has_repo_write_permission(
        owner=pr.owner, repo=pr.repo, username=login, token=token
    ):
        return VoteOutcome(f"@{login} does not have write access to {pr.owner}/{pr.repo}.")
    return Voter(user=user, github_login=login)


async def handle_vote(
    approval: ExpeditedApproval,
    *,
    decision: CardAction,
    user: User | None,
) -> VoteOutcome:
    """Record one click. Slow work runs unlocked; the row lock covers only the write."""
    if approval.state != "open":
        return VoteOutcome("This expedited review is no longer accepting votes.")
    voter = await _resolve_voter(approval, user)
    if isinstance(voter, VoteOutcome):
        return voter
    authored = approval.is_author(voter.user.id, voter.github_login)

    if decision == "ready":
        if not authored:
            return VoteOutcome("Only the pull request's author can mark it ready for review.")
        return await _mark_ready(approval, voter=voter)
    if decision == "reject":
        return await _reject(approval, voter=voter)

    if approval.awaiting_ready:
        return VoteOutcome("The author has to mark this draft ready for review first.")
    if authored:
        return VoteOutcome("You authored this pull request; someone else has to approve it.")
    if approval.vote_by(voter.user.id) is not None:
        return VoteOutcome("You already approved this revision.")
    if not await get_valid_access_token(voter.github_login):
        return VoteOutcome(
            f"Open SWE has no GitHub token for @{voter.github_login}, so it could not submit "
            f"your review at merge time. {github_token_hint()}"
        )

    async with ExpeditedApproval.locked(approval.id) as (_, row):
        if row is None or row.state != "open":
            return VoteOutcome("This expedited review closed before your vote was recorded.")
        first_approval = not row.approved
        if row.vote_by(voter.user.id) is None:
            row.votes.append(ApprovalVote(voter_user_id=voter.user.id, decision="approve"))
    current = await ExpeditedApproval.get(approval.id)
    if current is None:
        return VoteOutcome("This expedited review vanished.")
    await refresh_card(current)
    if first_approval:
        pr = current.pull_request
        await notify_agent(
            current,
            render_prompt(
                "runs/expedited-review-approved.md",
                pr_url=pr.url,
                approvers=", ".join(f"@{login}" for login in current.approvers),
            ),
        )
    return VoteOutcome(f"Approval recorded as @{voter.github_login}.")


async def _mark_ready(approval: ExpeditedApproval, *, voter: Voter) -> VoteOutcome:
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
    current = await ExpeditedApproval.get(approval.id)
    if current is not None:
        await refresh_card(current)
    return VoteOutcome("Marked ready for review. Someone else can approve it now.")


async def _reject(approval: ExpeditedApproval, *, voter: Voter) -> VoteOutcome:
    """Close the card; anyone with something to say tags the agent in the thread."""
    async with ExpeditedApproval.locked(approval.id) as (_, row):
        if row is None or row.state != "open":
            return VoteOutcome("This expedited review is no longer accepting votes.")
        row.votes = [vote for vote in row.votes if vote.voter_user_id != voter.user.id]
        row.votes.append(ApprovalVote(voter_user_id=voter.user.id, decision="reject"))
    await retire(approval, "rejected", f"Rejected by @{voter.github_login}.")
    return VoteOutcome("Rejected. Tag the agent in the thread to tell it what to change.")


async def process_vote(
    approval_id: str,
    *,
    decision: CardAction,
    person: PersonIdentity,
    channel_id: str,
    thread_ts: str,
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
            outcome = await handle_vote(
                approval,
                decision=decision,
                user=await User.for_person(person),
            )
    except Exception:
        logger.exception("Expedited review vote failed", extra={"approval_id": approval_id})
        outcome = VoteOutcome("Something went wrong recording your vote. Try again.")
    await post_slack_ephemeral_message(channel_id, slack_user_id, outcome.message, thread_ts)
