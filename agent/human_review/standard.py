"""Standard human review: ask a repository's review channel to review a pull request.

A request is refused while the pull request is closed, a draft, conflicted, or
failing a required check. People sign up from the card; after
``UNCLAIMED_AFTER_MINUTES`` with nobody signed up the agent picks someone. The
pull request merges once every reviewer approves on GitHub, or once
``AUTO_MERGE_AFTER_HOURS`` have passed with at least one approval, and only while
it is otherwise ready.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import httpx2
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from agent.expedited_review.readiness import (
    PullRequestSnapshot,
    assess_readiness,
    latest_review_states,
)
from agent.github.ci import fetch_pr
from agent.github.http import GITHUB_API_BASE, github_client, github_request
from agent.github.pull_requests import PullRequest, PullRequestPayload
from agent.github.repo_files import RepoSettings
from agent.human_review.card import mention
from agent.human_review.lifecycle import (
    mark_merged,
    notify_agent,
    post_standard_card,
    refresh_card,
    retire,
)
from agent.human_review.merging import merge_pull_request
from agent.human_review.people import Outcome, Participant, repo_token, resolve_writer
from agent.human_review.requests import HumanReviewParticipant, HumanReviewRequest
from agent.human_review.tldr import pull_request_tldr
from agent.prompts import prompt
from agent.slack.blocks import escape
from agent.slack.channels import SlackChannel
from agent.slack.client import GitHubPrRef, get_slack_permalink, post_slack_thread_reply_with_ts
from agent.slack.dm import send_dm
from agent.threads.pr_fixes import dispatch_pull_request_prompt
from agent.users import User
from agent.utils.json_types import JsonObject
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

SCHEDULER_TASK = "human_review"
UNCLAIMED_AFTER_MINUTES = 30
AUTO_MERGE_AFTER_HOURS = 2
DeadlineStep = Literal["unclaimed", "auto_merge"]


class RequestResult(BaseModel):
    success: bool
    error: str = ""
    request_id: str = ""
    channel: str = ""
    permalink: str = ""
    reused: bool = False


@dataclass(frozen=True, slots=True)
class Origin:
    """Who asked and from where; an agent run carries its thread and Slack location."""

    requester: User | None = None
    thread_id: str = ""
    run_config: JsonObject = field(default_factory=dict)
    slack_channel_id: str = ""
    slack_thread_ts: str = ""


def _failure(error: str) -> RequestResult:
    return RequestResult(success=False, error=error)


def request_blockers(snapshot: PullRequestSnapshot) -> list[str]:
    """Why a pull request may not be put up for review yet."""
    blockers: list[str] = []
    if snapshot.merged:
        blockers.append("it is already merged")
    elif snapshot.state != "open":
        blockers.append("it is closed")
    if snapshot.draft:
        blockers.append("it is a draft")
    if snapshot.mergeable is False or snapshot.mergeable_state == "dirty":
        blockers.append("it has merge conflicts")
    if snapshot.check_state in {"failure", "blocked"} and snapshot.failures_are_required:
        names = ", ".join(snapshot.failing_checks) or "a required check"
        blockers.append(f"required checks are failing: {names}")
    return blockers


async def _target_channel(
    pr_ref: GitHubPrRef, override: str, token: str
) -> SlackChannel | RequestResult:
    configured = override.strip()
    if not configured:
        configured = (
            await RepoSettings.fetch(pr_ref.owner, pr_ref.repo, token=token)
        ).review_channel
    if not configured.strip():
        return _failure(
            f"{pr_ref.owner}/{pr_ref.repo} has no review channel. Set `reviewChannel` in "
            "`.open-swe/settings.json` on the default branch, or name a Slack channel."
        )
    channel = await SlackChannel.resolve(configured)
    if channel is None:
        return _failure(f"Slack channel {configured.strip()!r} was not found.")
    return channel


async def _permalink(request: HumanReviewRequest) -> str:
    return await get_slack_permalink(request.slack_channel_id, request.slack_message_ts) or ""


async def _schedule(request: HumanReviewRequest, step: DeadlineStep, after: timedelta) -> bool:
    try:
        await langgraph_client().runs.create(
            None,
            "scheduler",
            input={"task": SCHEDULER_TASK, "request_id": str(request.id), "step": step},
            metadata={"kind": SCHEDULER_TASK, "request_id": str(request.id), "step": step},
            after_seconds=int(after.total_seconds()),
            on_completion="delete",
        )
    except Exception:
        logger.warning(
            "Could not schedule a human review deadline",
            extra={"request_id": str(request.id), "step": step},
            exc_info=True,
        )
        return False
    return True


async def _existing(active: HumanReviewRequest) -> RequestResult:
    if active.kind == "expedited":
        return _failure(
            "This pull request has an open expedited review card. Dismiss it before "
            "asking for a standard review."
        )
    return RequestResult(
        success=True,
        request_id=str(active.id),
        channel=active.slack_channel_id,
        permalink=await _permalink(active),
        reused=True,
    )


async def request_review(
    pr_ref: GitHubPrRef, origin: Origin, *, channel: str = ""
) -> RequestResult:
    """Post a standard review card for ``pr_ref``, or return the one already open."""
    token = await repo_token(pr_ref.owner, pr_ref.repo)
    if token is None:
        return _failure("Open SWE cannot reach this repository's GitHub App installation.")
    readiness = await assess_readiness(
        owner=pr_ref.owner, repo=pr_ref.repo, pr_number=pr_ref.number, token=token
    )
    if readiness is None:
        return _failure("GitHub was unavailable while checking the pull request.")
    if blockers := request_blockers(readiness.snapshot):
        return _failure(
            "The pull request cannot be put up for review: " + "; ".join(blockers) + "."
        )

    active = await HumanReviewRequest.active_for(pr_ref.owner, pr_ref.repo, pr_ref.number)
    if active is not None:
        return await _existing(active)

    target = await _target_channel(pr_ref, channel, token)
    if isinstance(target, RequestResult):
        return target
    payload = await fetch_pr(
        owner=pr_ref.owner, repo=pr_ref.repo, pr_number=pr_ref.number, token=token
    )
    if payload is None:
        return _failure("Pull request is unavailable")
    details = PullRequestPayload.model_validate(payload)

    pull_request = await PullRequest.load(pr_ref.owner, pr_ref.repo, pr_ref.number)
    pull_request.title = details.title
    pull_request.head_ref = details.head_ref
    pull_request.base_ref = details.base_ref
    pull_request.author = details.author
    pull_request.author_github_id = details.author_id
    pull_request.additions = details.additions
    pull_request.deletions = details.deletions
    pull_request.changed_files = details.changed_files
    pull_request = await pull_request.save()
    if origin.thread_id:
        pull_request = await pull_request.link_thread(origin.thread_id, source="human_review")

    in_thread = origin.slack_channel_id == target.id and bool(origin.slack_thread_ts)
    try:
        request = await HumanReviewRequest(
            pull_request_id=pull_request.id,
            head_sha=details.head_sha,
            kind="standard",
            thread_id=origin.thread_id,
            run_config=origin.run_config,
            requested_by_user_id=origin.requester.id if origin.requester is not None else None,
            tldr=await pull_request_tldr(details.title, details.body),
            slack_channel_id=target.id,
            slack_thread_ts=origin.slack_thread_ts if in_thread else "",
            slack_broadcast=in_thread,
        ).save()
    except IntegrityError:
        # A concurrent request opened one first; the partial unique index allows only one.
        winner = await HumanReviewRequest.active_for(pr_ref.owner, pr_ref.repo, pr_ref.number)
        if winner is None:
            raise
        return await _existing(winner)
    # Without its deadlines a request could wait forever on a reviewer who never approves.
    scheduled = await _schedule(
        request, "unclaimed", timedelta(minutes=UNCLAIMED_AFTER_MINUTES)
    ) and await _schedule(request, "auto_merge", timedelta(hours=AUTO_MERGE_AFTER_HOURS))
    if not scheduled:
        await _discard(request.id)
        return _failure("Open SWE could not schedule the review request's deadlines. Try again.")
    try:
        message_ts, error = await post_standard_card(request)
    except BaseException:
        await _discard(request.id)
        raise
    if not message_ts:
        await _discard(request.id)
        return _failure(
            f"Could not post in #{target.name or target.id}: {error or 'unknown error'}. "
            "For a private channel, invite the bot first."
        )
    request.slack_message_ts = message_ts
    request = await request.save()
    return RequestResult(
        success=True,
        request_id=str(request.id),
        channel=target.id,
        permalink=await _permalink(request),
    )


async def _discard(request_id: UUID) -> None:
    async with HumanReviewRequest.locked(request_id) as (session, row):
        if row is not None:
            await session.delete(row)


async def _request_github_review(request: HumanReviewRequest, login: str) -> bool:
    pr = request.pull_request
    token = await repo_token(pr.owner, pr.repo)
    if token is None:
        return False
    url = f"{GITHUB_API_BASE}/repos/{pr.owner}/{pr.repo}/pulls/{pr.number}/requested_reviewers"
    try:
        async with github_client(token=token) as client:
            response = await github_request(client, "POST", url, json={"reviewers": [login]})
    except httpx2.HTTPError:
        logger.warning(
            "GitHub review request did not complete",
            extra={"request_id": str(request.id)},
            exc_info=True,
        )
        return False
    if response.status_code not in {200, 201}:
        logger.warning(
            "GitHub refused a review request",
            extra={"request_id": str(request.id), "status_code": response.status_code},
        )
        return False
    return True


async def _add_reviewer(
    request: HumanReviewRequest, reviewer: Participant, *, assigned_by_agent: bool
) -> HumanReviewRequest | Outcome:
    if request.state != "open":
        return Outcome("This review request is closed.")
    if request.is_author(reviewer.user.id, reviewer.github_login):
        return Outcome("The author cannot review their own pull request.")
    async with HumanReviewRequest.locked(request.id) as (_, row):
        if row is None or row.state != "open":
            return Outcome("This review request closed before you signed up.")
        existing = row.participant(reviewer.user.id)
        if existing is not None:
            return Outcome(f"@{reviewer.github_login} is already reviewing this.")
        row.participants.append(
            HumanReviewParticipant(
                user_id=reviewer.user.id, decision="review", assigned_by_agent=assigned_by_agent
            )
        )
    current = await HumanReviewRequest.get(request.id)
    if current is None:
        return Outcome("This review request vanished.")
    await refresh_card(current)
    return current


async def claim(request: HumanReviewRequest, user: User | None) -> Outcome:
    """Someone signs up from the card; any number of people may."""
    reviewer = await resolve_writer(request, user)
    if isinstance(reviewer, Outcome):
        return reviewer
    added = await _add_reviewer(request, reviewer, assigned_by_agent=False)
    if isinstance(added, Outcome):
        return added
    requested = await _request_github_review(added, reviewer.github_login)
    pr = added.pull_request
    note = "" if requested else " GitHub did not add you as a requested reviewer."
    return Outcome(
        f"You're down to review <{pr.url}|{pr.owner}/{pr.repo}#{pr.number}>. It merges once "
        f"every reviewer approves on GitHub.{note}"
    )


async def assign(request: HumanReviewRequest, github_login: str, reason: str) -> RequestResult:
    """The agent's pick for a request nobody signed up for: tag them on the card and DM them."""
    user = await User.for_login("github", github_login)
    if user is None:
        return _failure(f"@{github_login} is not an Open SWE user; pick someone who is.")
    reviewer = await resolve_writer(request, user)
    if isinstance(reviewer, Outcome):
        return _failure(reviewer.message)
    added = await _add_reviewer(request, reviewer, assigned_by_agent=True)
    if isinstance(added, Outcome):
        return _failure(added.message)
    await _request_github_review(added, github_login)
    pr = added.pull_request
    label = f"<{pr.url}|{pr.owner}/{pr.repo}#{pr.number}>"
    who = mention(user)
    why = f" {escape(reason.strip())}" if reason.strip() else ""
    thread_ts = added.slack_thread_ts or added.slack_message_ts
    await post_slack_thread_reply_with_ts(
        added.slack_channel_id,
        thread_ts,
        f"{who}, nobody signed up to review {label}, so Open SWE picked you.{why}",
        unfurl_links=False,
        agent_thread_id=added.thread_id or None,
    )
    permalink = await _permalink(added)
    if user.slack_user_id:
        card = f" (<{permalink}|review card>)" if permalink else ""
        await send_dm(
            user.slack_user_id,
            f"Open SWE picked you to review {label} *{escape(pr.title)}*{card}.{why}",
        )
    return RequestResult(
        success=True,
        request_id=str(added.id),
        channel=added.slack_channel_id,
        permalink=permalink,
    )


async def _set_detail(request: HumanReviewRequest, detail: str) -> HumanReviewRequest:
    async with HumanReviewRequest.locked(request.id) as (_, row):
        if row is not None and row.state == "open":
            row.detail = detail
    return await HumanReviewRequest.get(request.id) or request


def merge_wait(
    reviewers: list[str], requested_at: datetime | None, states: dict[str, str], now: datetime
) -> str | None:
    """What approvals the merge still waits for, or ``None`` when they are enough.

    ``states`` maps each GitHub login to its latest review state.
    """
    approvers = {login.lower() for login, state in states.items() if state == "APPROVED"}
    if not approvers:
        return "an approval on GitHub"
    pending = [login for login in reviewers if login.lower() not in approvers]
    deadline_passed = requested_at is not None and now >= requested_at + timedelta(
        hours=AUTO_MERGE_AFTER_HOURS
    )
    if pending and not deadline_passed:
        return "approval from " + ", ".join(f"@{login}" for login in pending)
    return None


async def settle(request: HumanReviewRequest) -> None:
    """Re-read the pull request, update the card, and merge when the request is satisfied."""
    if request.kind != "standard" or request.state != "open":
        return
    pr = request.pull_request
    token = await repo_token(pr.owner, pr.repo)
    if token is None:
        return
    readiness = await assess_readiness(
        owner=pr.owner, repo=pr.repo, pr_number=pr.number, token=token
    )
    if readiness is None:
        return
    snapshot = readiness.snapshot
    if snapshot.merged:
        await mark_merged(request)
        return
    if snapshot.state != "open":
        await retire(request, "cancelled", "the pull request was closed")
        return
    async with github_client(token=token) as client:
        states = await latest_review_states(client, pr.owner, pr.repo, pr.number, snapshot.author)
    if states is None:
        return
    waiting = merge_wait(
        [reviewer.github_login for reviewer in request.reviewers],
        request.created_at,
        states,
        datetime.now(UTC),
    )
    if waiting is None and readiness.blockers:
        waiting = "; ".join(readiness.blockers)
    if waiting is not None:
        await refresh_card(await _set_detail(request, waiting))
        return
    async with HumanReviewRequest.locked(request.id) as (_, row):
        if row is None or row.state != "open":
            return
        result = await merge_pull_request(
            row, snapshot.head_sha, snapshot.allowed_merge_methods, token
        )
        if result.status != "merged":
            row.detail = result.message
    if result.status == "merged":
        await mark_merged(request)
        return
    logger.warning(
        "Auto-merge of a reviewed pull request was refused",
        extra={"request_id": str(request.id), "merge_status": result.status},
    )
    current = await HumanReviewRequest.get(request.id)
    if current is not None:
        await refresh_card(current)


async def settle_pull_request(owner: str, repo: str, number: int) -> None:
    request = await HumanReviewRequest.active_for(owner, repo, number)
    if request is not None:
        await settle(request)


async def settle_repository(owner: str, repo: str) -> None:
    """Re-check every open standard request in a repository, for events that name no PR."""
    for request in await HumanReviewRequest.open_in_repository(owner, repo, kind="standard"):
        await settle(request)


async def _wake_for_reviewer(request: HumanReviewRequest) -> bool:
    pr = request.pull_request
    text = prompt(
        "runs/human-review-unclaimed",
        pr_url=pr.url,
        minutes=UNCLAIMED_AFTER_MINUTES,
        author=pr.author,
    )
    if request.thread_id:
        return await notify_agent(request, text)
    requester = request.requested_by
    login = requester.login_for("github") if requester is not None else ""
    if not login:
        logger.info(
            "Unclaimed review request has no agent thread or requester to wake",
            extra={"request_id": str(request.id)},
        )
        return False

    async def record_thread(thread_id: str) -> None:
        async with HumanReviewRequest.locked(request.id) as (_, row):
            if row is not None:
                row.thread_id = thread_id

    await dispatch_pull_request_prompt(
        pr.owner,
        pr.repo,
        pr.number,
        login,
        text,
        title=f"Pick a reviewer for {pr.repo}#{pr.number}",
        before_dispatch=record_thread,
    )
    return True


async def run_deadline(request_id: str, step: str) -> dict[str, str]:
    """Scheduler entry point for the unclaimed and auto-merge deadlines."""
    try:
        request = await HumanReviewRequest.get(UUID(request_id))
    except ValueError:
        request = None
    if request is None or request.state != "open":
        return {"status": "closed"}
    if step == "unclaimed":
        if request.reviewers:
            return {"status": "claimed"}
        return {"status": "woken" if await _wake_for_reviewer(request) else "not_woken"}
    if step == "auto_merge":
        await settle(request)
        return {"status": "settled"}
    return {"status": "unknown_step"}
