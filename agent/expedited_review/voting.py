"""Approve and Reject clicks on an expedited review card.

A voter is a person (``users`` row) reached through their Slack identity whose
GitHub identity has write access to the repository. A click only records the
vote; the agent turns approvals into GitHub reviews when it merges.
"""

import logging
from dataclasses import dataclass
from uuid import UUID

from agent.dashboard.profiles import get_valid_access_token
from agent.expedited_review.approvals import (
    REQUIRED_APPROVALS,
    ApprovalVote,
    ExpeditedApproval,
    VoteDecision,
)
from agent.expedited_review.lifecycle import notify_agent, refresh_card, repo_token, retire
from agent.github.ci import has_repo_write_permission
from agent.input_messages import PersonIdentity, split_person_id
from agent.prompts import render_prompt
from agent.slack.client import post_slack_ephemeral_message, slack_thread_mutation_lock
from agent.users import User
from agent.utils.dashboard_links import dashboard_base_url
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)


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


def is_author(approval: ExpeditedApproval, user_id: UUID, login: str) -> bool:
    pr = approval.pull_request
    if pr.author_user_id is not None:
        return pr.author_user_id == user_id
    return bool(pr.author) and pr.author.lower() == login.lower()


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

    if decision == "reject":
        return await _reject(approval, voter=voter, feedback=feedback)

    if approval.vote_by(voter.user.id) is not None:
        return VoteOutcome("You already approved this revision.")
    if not is_author(approval, voter.user.id, voter.github_login) and not (
        await get_valid_access_token(voter.github_login)
    ):
        return VoteOutcome(
            f"Open SWE has no GitHub token for @{voter.github_login}, so it could not submit "
            f"your review at merge time. {github_token_hint()}"
        )

    async with ExpeditedApproval.locked(approval.id) as (_, row):
        if row is None or row.state != "open":
            return VoteOutcome("This expedited review closed before your vote was recorded.")
        if row.vote_by(voter.user.id) is None:
            row.votes.append(ApprovalVote(voter_user_id=voter.user.id, decision="approve"))
        reached_quorum = len(row.approvals) == REQUIRED_APPROVALS
    current = await ExpeditedApproval.get(approval.id)
    if current is None:
        return VoteOutcome("This expedited review vanished.")
    await refresh_card(current)
    if reached_quorum:
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
        agent_prompt=render_prompt(
            "runs/expedited-review-rejected.md",
            pr_url=pr.url,
            rejector=login,
            feedback=clean_feedback or "(none given)",
        ),
    )
    return VoteOutcome("Rejected. The agent has been told.")


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
                feedback=feedback,
            )
    except Exception:
        logger.exception("Expedited review vote failed", extra={"approval_id": approval_id})
        outcome = VoteOutcome("Something went wrong recording your vote. Try again.")
    await post_slack_ephemeral_message(channel_id, slack_user_id, outcome.message, thread_ts)
