"""Mark ready, Approve, Broadcast, Send and Dismiss clicks on an expedited review card.

A voter is a person (``users`` row) reached through their Slack identity whose
GitHub identity has write access to the repository. Only the author may mark a
draft ready, and only someone else may approve. An approval is submitted as the
voter's GitHub review as soon as it is recorded; the agent merges later.
"""

import logging
from typing import Literal
from uuid import UUID

from fastapi import HTTPException

from openswe.dashboard.profiles import get_valid_access_token
from openswe.expedited_review.channels import sendable_channel, still_internal
from openswe.expedited_review.eligibility import ChangedFile, fingerprint_matches
from openswe.expedited_review.reviews import github_token_hint, submit_approval
from openswe.github.http import (
    GitHubAppUnavailable,
    GitHubClient,
    GitHubSignInRequired,
    or_none,
)
from openswe.github.pull_request_actions import MarkReadyAction, act_on_pull_request
from openswe.human_review.clicks import answer_click
from openswe.human_review.lifecycle import (
    broadcast_card,
    copy_card,
    dismiss_request,
    notify_agent,
    refresh_card,
    refresh_card_in_thread,
)
from openswe.human_review.people import (
    Outcome,
    Participant,
    linked_participant,
    resolve_writer,
)
from openswe.human_review.requests import HumanReviewParticipant, HumanReviewRequest
from openswe.input_messages import PersonIdentity, split_person_id
from openswe.prompts import prompt
from openswe.slack.dm import note_for_concierge
from openswe.users import User

logger = logging.getLogger(__name__)

VoteAction = Literal["approve", "ready"]
CardAction = VoteAction | Literal["dismiss", "broadcast", "send"]


async def handle_vote(
    approval: HumanReviewRequest,
    *,
    decision: VoteAction,
    user: User | None,
) -> Outcome:
    """Record one click. Slow work runs unlocked; the row lock covers only the write."""
    if approval.state != "open":
        return Outcome("This expedited review is no longer accepting votes.")
    if decision == "ready":
        # The author's own token decides; a fork's author has no write access upstream.
        author = linked_participant(user)
        if isinstance(author, Outcome):
            return author
        if not approval.is_author(author.user.id, author.github_login):
            return Outcome("Only the pull request's author can mark it ready for review.")
        return await _mark_ready(approval, voter=author)
    voter = await resolve_writer(approval, user)
    if isinstance(voter, Outcome):
        return voter
    authored = approval.is_author(voter.user.id, voter.github_login)

    if approval.awaiting_ready:
        return Outcome("The author has to mark this draft ready for review first.")
    if authored:
        return Outcome("You authored this pull request; someone else has to approve it.")
    if approval.participant(voter.user.id) is not None:
        return Outcome("You already approved this revision.")
    if not await get_valid_access_token(voter.github_login):
        return Outcome(
            f"Open SWE has no GitHub token for @{voter.github_login}, so it cannot submit "
            f"your review. {github_token_hint()}"
        )

    async with HumanReviewRequest.locked(approval.id) as (_, row):
        if row is None or row.state != "open":
            return Outcome("This expedited review closed before your vote was recorded.")
        first_approval = not row.approved
        added = row.participant(voter.user.id) is None
        if added:
            row.participants.append(
                HumanReviewParticipant(user_id=voter.user.id, decision="approve")
            )
    current = await HumanReviewRequest.get(approval.id)
    if current is None:
        return Outcome("This expedited review vanished.")
    problem = await _submit_review(current, voter.user.id) if added else None
    await refresh_card_in_thread(current)
    if first_approval and not await notify_agent(
        current,
        prompt(
            "runs/expedited-review-approved",
            pr_url=current.pull_request.url,
            approvers=", ".join(f"@{login}" for login in current.approvers),
        ),
    ):
        woken = "Open SWE could not be woken to merge it; tag it in the thread to try again."
        return Outcome(f"{problem} {woken}" if problem else woken)
    return Outcome(problem or "")


async def _submit_review(approval: HumanReviewRequest, voter_user_id: UUID) -> str | None:
    """Send a new vote to GitHub now; what kept it off GitHub, or ``None`` once it is there.

    A vote that could not be sent stays recorded, and the merge submits it. The POST
    runs under the card's row lock, which the merge also holds, so one of them submits.
    """
    pr = approval.pull_request
    unavailable = "GitHub was unavailable, so Open SWE will submit your review when it merges."
    try:
        async with GitHubClient.as_app(pr.owner, pr.repo) as github:
            pull = github.repo(pr.owner, pr.repo).pull_request(pr.number)
            head_sha = await or_none(pull.head_sha())
            files = await ChangedFile.of_pull(pull)
    except GitHubAppUnavailable:
        return unavailable
    if head_sha is None or files is None:
        return unavailable
    if not fingerprint_matches(files, approval.diff_fingerprint):
        return "A later commit changed the diff on this card, so no review was submitted."
    async with HumanReviewRequest.locked(approval.id) as (_, row):
        if row is None or row.state != "open":
            return "The card closed before your review reached GitHub."
        vote = row.participant(voter_user_id)
        if vote is None:
            return "This expedited review vanished."
        if vote.github_review_id is not None:
            return None
        failed = await submit_approval(row, vote, head_sha)
    if failed is not None:
        return f"{failed} Open SWE will try again when it merges."
    return None


async def _mark_ready(approval: HumanReviewRequest, *, voter: Participant) -> Outcome:
    """Undraft the PR as its author, then open the card for approval."""
    if not approval.awaiting_ready:
        return Outcome("This pull request is already ready for review.", dm_card_success=True)
    pr = approval.pull_request
    try:
        async with GitHubClient.as_user(voter.github_login) as github:
            await act_on_pull_request(
                github.repo(pr.owner, pr.repo).pull_request(pr.number),
                MarkReadyAction(action="mark-ready"),
            )
    except GitHubSignInRequired:
        return Outcome(
            f"Open SWE has no GitHub token for @{voter.github_login}, so it cannot mark the "
            f"pull request ready. {github_token_hint()}"
        )
    except HTTPException as exc:
        return Outcome(f"GitHub did not mark the pull request ready: {exc.detail}")
    async with HumanReviewRequest.locked(approval.id) as (_, row):
        if row is None or row.state != "open":
            return Outcome("This expedited review closed before it was marked ready.")
        row.awaiting_ready = False
    marked = "Marked ready for review. Someone else can approve it now."
    current = await HumanReviewRequest.get(approval.id)
    if current is None:
        return Outcome(marked, dm_card_success=True)
    await refresh_card(current)
    return Outcome(marked, dm_card_success=True)


async def request_broadcast(approval: HumanReviewRequest) -> Outcome:
    """Anyone in the thread may send an open card to the channel."""
    if approval.state != "open" or approval.approved:
        return Outcome("This expedited review is no longer waiting for approval.")
    if approval.sent_elsewhere:
        return Outcome("This expedited review was already sent to a channel.")
    choices = approval.slack_channel_choices
    if len(choices) == 1 and choices[0]["id"] != approval.slack_channel_id:
        return await request_copy(approval, choices[0]["id"], None, configured=True)
    if not await broadcast_card(approval):
        return Outcome("Open SWE could not send this expedited review to the channel.")
    return Outcome("Sent to the channel.")


async def request_copy(
    approval: HumanReviewRequest, channel_id: str, user: User | None, *, configured: bool = False
) -> Outcome:
    """Only someone with write access may show the diff to another channel's members."""
    # A modal can be submitted long after it opened, past the click's own channel check.
    if not await still_internal(approval.slack_channel_id):
        return Outcome("This card's channel is shared outside the workspace, so it cannot be sent.")
    if channel_id == approval.slack_channel_id:
        return await request_broadcast(approval)
    if approval.state != "open" or approval.approved:
        return Outcome("This expedited review is no longer waiting for approval.")
    if approval.sent_elsewhere:
        return Outcome("This expedited review was already sent to a channel.")
    if not configured:
        sender = await resolve_writer(approval, user)
        if isinstance(sender, Outcome):
            return sender
    channel = await sendable_channel(channel_id)
    if channel is None:
        return Outcome(
            "Open SWE only sends expedited reviews to public channels that are not shared "
            "outside the workspace."
        )
    if (problem := await copy_card(approval, channel)) is not None:
        return Outcome(
            f"Open SWE could not send this expedited review to <#{channel.id}>. {problem}"
        )
    return Outcome(f"Sent to <#{channel.id}>.")


async def process_vote(
    approval_id: str,
    *,
    decision: CardAction,
    person: PersonIdentity,
    channel_id: str,
    thread_ts: str,
    target_channel: str = "",
) -> None:
    """Background entry point for a Slack click; answers the clicker ephemerally.

    ``target_channel`` is the channel a ``send`` click picked.
    """
    slack_user_id = split_person_id(person)[1]

    async def handle(approval: HumanReviewRequest) -> Outcome:
        if channel_id == approval.slack_dm_channel_id:
            await note_for_concierge(
                slack_user_id,
                channel_id,
                f"Clicked {decision} on the expedited review card for {approval.pull_request.url}.",
            )
        match decision:
            case "dismiss":
                return await dismiss_request(approval, slack_user_id)
            case "broadcast":
                return await request_broadcast(approval)
            case "send":
                return await request_copy(approval, target_channel, await User.for_person(person))
            case _:
                return await handle_vote(
                    approval, decision=decision, user=await User.for_person(person)
                )

    await answer_click(
        approval_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        slack_user_id=slack_user_id,
        handle=handle,
    )
