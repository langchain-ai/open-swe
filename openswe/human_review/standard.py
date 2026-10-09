"""Standard human review: ask a repository's review channel to review a pull request.

A request is refused while the pull request is closed, a draft, conflicted, or
failing a required check. People sign up from the card; after
the workspace auto-assignment timeout with nobody signed up Open SWE picks someone (see
``openswe.human_review.picking``), waking an agent to pick when nobody qualifies. The
pull request merges once every reviewer approves on GitHub, or once
``AUTO_MERGE_AFTER_HOURS`` have passed with at least one approval, and only while
it is otherwise ready.

A ``posted`` request settles here too but never merges: its message gets an
approved reaction, and once its pull request has sat green and unapproved for
the workspace auto-assignment timeout the agent picks a reviewer, the same way.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx2
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from openswe.expedited_review.eligibility import MAX_FILES, ChangedFile
from openswe.expedited_review.readiness import (
    PullRequestSnapshot,
    Readiness,
    latest_review_states,
    review_authors,
)
from openswe.github.codeowners import CodeOwners
from openswe.github.http import (
    GitHubAppUnavailable,
    GitHubClient,
    GitHubError,
    RepoClient,
    or_none,
)
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequest, PullRequestPayload
from openswe.github.repo_files import RepoFileUnreadableError, RepoSettings
from openswe.human_review.card import accept_button, decline_button, mention, snooze_button
from openswe.human_review.lifecycle import ReviewCard, ReviewPicks
from openswe.human_review.merging import merge_pull_request
from openswe.human_review.people import Outcome, Participant, resolve_writer
from openswe.human_review.pick_message import PickMessage
from openswe.human_review.picking import Area, Coverage, Pick, Wait, choose_reviewer
from openswe.human_review.requests import HumanReviewParticipant, HumanReviewRequest, RequestKind
from openswe.prompts import prompt
from openswe.run_config import RunConfig
from openswe.slack.blocks import actions, block_payload, escape, section
from openswe.slack.cards import origin_footer
from openswe.slack.channels import SlackChannel
from openswe.slack.client import (
    GitHubPrRef,
    get_slack_permalink,
    get_slack_user_info,
    post_slack_thread_reply_with_ts,
    remove_slack_reaction,
)
from openswe.slack.dm import send_dm, send_dm_with_location
from openswe.slack.http import SlackRequestError
from openswe.slack.thread_owner import wake_thread_owner
from openswe.threads.pr_fixes import dispatch_pull_request_prompt
from openswe.users import User
from openswe.utils.json_types import JsonObject
from openswe.utils.preview import skip_on_preview
from openswe.utils.thread_ops import langgraph_client
from openswe.web.workspace_settings import get_workspace_settings
from openswe.workspaces.routing import resolve_workspace

logger = logging.getLogger(__name__)

SCHEDULER_TASK = "human_review"
AUTO_MERGE_AFTER_HOURS = 2
SUMMARY_MAX_CHARS = 280
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
DeadlineStep = Literal["unclaimed", "pick_expiry", "auto_merge"]
SETTLED_KINDS: tuple[RequestKind, ...] = ("standard", "posted")
# A deadline run may start a little before the wait its timer was set for has passed.
_SCHEDULER_EARLINESS = timedelta(minutes=1)
_DEADLINE_RETRY = timedelta(minutes=5)
SNOOZE_DURATIONS = {
    "30 minutes": timedelta(minutes=30),
    "1 hour": timedelta(hours=1),
    "2 hours": timedelta(hours=2),
    "4 hours": timedelta(hours=4),
    "1 day": timedelta(days=1),
    "2 days": timedelta(days=2),
}
_AUTO_ASSIGN_ASKED = "auto_assign_asked"
_REMAINING_OWNERS_ASKED = "remaining_owners_asked"


class ReviewChannelUnknownError(Exception):
    """The repository's review channels could not be read, so membership is unknown."""


async def in_review_channel(repo: RepoClient, channel_id: str) -> bool:
    """Whether ``channel_id`` is one of the repository's configured review channels."""
    try:
        settings = await RepoSettings.fetch(repo, strict=True)
    except RepoFileUnreadableError as exc:
        raise ReviewChannelUnknownError(str(exc)) from exc
    unresolved: list[str] = []
    for configured in settings.review_channels:
        channel = await SlackChannel.resolve(configured)
        if channel is None:
            unresolved.append(configured)
        elif channel.id == channel_id:
            return True
    if unresolved:
        raise ReviewChannelUnknownError(f"Could not resolve review channels {unresolved}")
    return False


async def _assignment_minutes(request: HumanReviewRequest) -> int:
    workspace = RunConfig.parse(request.run_config).workspace_slug
    pr = request.pull_request
    resolved = await resolve_workspace(
        thread_workspace=workspace,
        slack_channel_id=request.slack_channel_id,
        repo=(pr.owner, pr.repo),
    )
    return (await get_workspace_settings(resolved.slug)).human_review_auto_assign_minutes


class RequestResult(BaseModel):
    success: bool
    error: str = ""
    request_id: str = ""
    channel: str = ""
    permalink: str = ""
    reused: bool = False
    summary_updated: bool = False
    claimed: bool = False


@dataclass(frozen=True, slots=True)
class Origin:
    """Who asked and from where; an agent run carries its thread and Slack location."""

    requester: User | None = None
    thread_id: str = ""
    run_config: JsonObject = field(default_factory=dict)
    slack_channel_id: str = ""
    slack_thread_ts: str = ""

    def asked(self, request: HumanReviewRequest) -> bool:
        """Whether ``request`` came from this thread or this person."""
        if self.thread_id and request.thread_id == self.thread_id:
            return True
        return self.requester is not None and request.requested_by_user_id == self.requester.id


@dataclass(frozen=True, slots=True)
class AlreadyClaimed(Outcome):
    pass


def _failure(error: str) -> RequestResult:
    return RequestResult(success=False, error=error)


def summary_line(text: str) -> str:
    """``text`` on one line, cut at a word boundary to fit the card."""
    flat = " ".join(_HTML_COMMENT.sub("", text).split())
    if len(flat) <= SUMMARY_MAX_CHARS:
        return flat
    return flat[: SUMMARY_MAX_CHARS - 1].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"


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
    pr_ref: GitHubPrRef, pull: PullRequestClient, override: str
) -> SlackChannel | RequestResult:
    configured = override.strip()
    if not configured:
        settings = await RepoSettings.cached(pr_ref.owner, pr_ref.repo)
        try:
            configured = await settings.channel_for_pr(pull)
        except httpx2.HTTPError, ValueError:
            logger.exception("Could not resolve review channel from changed files")
            return _failure(
                "Could not read the complete changed-file list for review channel routing."
            )
    if not configured.strip():
        return _failure(
            f"{pr_ref.owner}/{pr_ref.repo} has no review channel. Set `reviewChannel` in "
            "`.open-swe/settings.json`, or name a Slack channel."
        )
    channel = await SlackChannel.resolve(configured)
    if channel is None:
        return _failure(f"Slack channel {configured.strip()!r} was not found.")
    return channel


async def _point_to_card(request: HumanReviewRequest, origin: Origin, target: SlackChannel) -> None:
    """Tell the asking thread where the card went, since a broadcast cannot reach another channel."""
    # A channel mention, not the card's permalink: Slack unfurls that into a second card.
    try:
        await post_slack_thread_reply_with_ts(
            origin.slack_channel_id,
            origin.slack_thread_ts,
            f"Review requested in <#{target.id}>.",
            unfurl_links=False,
            unfurl_media=False,
        )
    except SlackRequestError as exc:
        logger.warning(
            "Could not point the asking thread at its review card",
            extra={"request_id": str(request.id), "slack_error": exc.code},
        )


async def _permalink(request: HumanReviewRequest) -> str:
    return await get_slack_permalink(request.slack_channel_id, request.slack_message_ts) or ""


def review_reminder_at(start: datetime, timezone: ZoneInfo) -> datetime:
    """Add two hours within local weekday 9am–6pm windows."""
    cursor = start.astimezone(timezone)
    remaining = timedelta(hours=2)
    while True:
        opening = datetime.combine(cursor.date(), time(9), timezone)
        closing = datetime.combine(cursor.date(), time(18), timezone)
        if cursor.weekday() >= 5 or cursor >= closing:
            cursor = datetime.combine(cursor.date() + timedelta(days=1), time(9), timezone)
            continue
        cursor = max(cursor, opening)
        available = closing - cursor
        if remaining <= available:
            return (cursor + remaining).astimezone(UTC)
        remaining -= available
        cursor = datetime.combine(cursor.date() + timedelta(days=1), time(9), timezone)


async def _schedule(request: HumanReviewRequest, step: str, after: timedelta) -> bool:
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
        permalink = await _permalink(active)
        return RequestResult(
            success=False,
            error=(
                "This pull request has an open expedited review card. Dismiss it before "
                "asking for a standard review."
                + (f" Open in Slack: {permalink}" if permalink else "")
            ),
            request_id=str(active.id),
            channel=active.slack_channel_id,
            permalink=permalink,
        )
    return RequestResult(
        success=True,
        request_id=str(active.id),
        channel=active.slack_channel_id,
        permalink=await _permalink(active),
        reused=True,
    )


async def _resummarize(active: HumanReviewRequest, tldr: str) -> RequestResult:
    """Whoever asked replaces their own card's summary."""
    if tldr == active.tldr:
        return await _existing(active)
    async with HumanReviewRequest.locked(active.id) as (_, row):
        if row is None or row.state != "open":
            return _failure("This review request closed before its summary could change.")
        row.tldr = tldr
        # Under the lock, so a concurrent dismissal waits and renders its closed card last.
        await ReviewCard(row).refresh()
    current = await HumanReviewRequest.get(active.id)
    if current is None:
        return _failure("This review request vanished.")
    result = await _existing(current)
    result.summary_updated = True
    return result


async def record_pull_request(
    pull: PullRequestClient,
) -> tuple[PullRequest, PullRequestPayload] | None:
    """Fetch the pull request and save what a review request shows of it; ``None`` if unavailable."""
    payload = await or_none(pull.pull())
    if payload is None:
        return None
    details = PullRequestPayload.model_validate(payload)
    pull_request = await PullRequest.load(pull.repo.owner, pull.repo.name, pull.number)
    pull_request.title = details.title
    pull_request.body = details.body or ""
    pull_request.head_ref = details.head_ref
    pull_request.base_ref = details.base_ref
    pull_request.author = details.author
    pull_request.author_github_id = details.author_id
    pull_request.additions = details.additions
    pull_request.deletions = details.deletions
    pull_request.changed_files = details.changed_files
    return await pull_request.save(), details


async def request_review(
    pr_ref: GitHubPrRef, origin: Origin, *, channel: str = "", inline_summary: str | None = None
) -> RequestResult:
    """Post a standard review card for ``pr_ref``, or return the one already open.

    ``inline_summary`` is the card's summary; ``None`` shows the start of the PR description.
    """
    try:
        async with PullRequestClient.as_app(pr_ref.owner, pr_ref.repo, pr_ref.number) as pull:
            return await _request_review(
                pr_ref, pull, origin, channel=channel, inline_summary=inline_summary
            )
    except GitHubAppUnavailable:
        return _failure("Open SWE cannot reach this repository's GitHub App installation.")


async def _request_review(
    pr_ref: GitHubPrRef,
    pull: PullRequestClient,
    origin: Origin,
    *,
    channel: str,
    inline_summary: str | None,
) -> RequestResult:
    # An open card stays correctable whatever has happened to the pull request since.
    active = await HumanReviewRequest.active_for(pr_ref.owner, pr_ref.repo, pr_ref.number)
    if active is not None:
        if active.kind == "standard" and inline_summary is not None and origin.asked(active):
            return await _resummarize(active, summary_line(inline_summary))
        return await _existing(active)

    readiness = await Readiness.assess(pull)
    if readiness is None:
        return _failure("GitHub was unavailable while checking the pull request.")
    if blockers := request_blockers(readiness.snapshot):
        return _failure(
            f"{pr_ref.url} cannot be put up for review: "
            + "; ".join(blockers)
            + ". "
            + prompt("tools/human-review-blocked")
        )

    target = await _target_channel(pr_ref, pull, channel)
    if isinstance(target, RequestResult):
        return target
    recorded = await record_pull_request(pull)
    if recorded is None:
        return _failure("Pull request is unavailable")
    pull_request, details = recorded
    if await User.for_login("github", details.author) is None:
        return _failure("Human review is only available for PRs authored by Open SWE users.")
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
            tldr=summary_line(details.body or "" if inline_summary is None else inline_summary),
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
        request, "unclaimed", timedelta(minutes=await _assignment_minutes(request))
    ) and await _schedule(request, "auto_merge", timedelta(hours=AUTO_MERGE_AFTER_HOURS))
    if not scheduled:
        await _discard(request.id)
        return _failure("Open SWE could not schedule the review request's deadlines. Try again.")
    try:
        message_ts = await ReviewCard(request).post_standard()
    except SlackRequestError as exc:
        await _discard(request.id)
        return _failure(
            f"Could not post in #{target.name or target.id}: {exc.code}. "
            "For a private channel, invite the bot first."
        )
    except BaseException:
        await _discard(request.id)
        raise
    request.slack_message_ts = message_ts
    request = await request.save()
    if not in_thread and origin.slack_channel_id and origin.slack_thread_ts:
        await _point_to_card(request, origin, target)
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
    try:
        async with PullRequestClient.as_app(pr.owner, pr.repo, pr.number) as pull:
            await pull.request_reviewers([login])
    except GitHubAppUnavailable:
        return False
    except GitHubError as refused:
        logger.warning(
            "GitHub refused a review request",
            extra={
                "request_id": str(request.id),
                "status_code": refused.response.status_code,
                "github_message": refused.message,
            },
        )
        return False
    except httpx2.HTTPError:
        logger.warning(
            "GitHub review request did not complete",
            extra={"request_id": str(request.id)},
            exc_info=True,
        )
        return False
    return True


async def _add_reviewer(
    request: HumanReviewRequest, reviewer: Participant, *, picked: bool, alongside: bool = False
) -> HumanReviewRequest | Outcome:
    """Sign ``reviewer`` up, or record Open SWE's pick of them.

    A pick signing up accepts it, and someone whose pick expired may still sign up themselves.
    ``alongside`` picks them next to existing reviewers, for code those reviewers do not own.
    """
    if request.state != "open":
        return Outcome("This review request is closed.")
    if request.is_author(reviewer.user.id, reviewer.github_login):
        return Outcome("The author cannot review their own pull request.")
    async with HumanReviewRequest.locked(request.id) as (_, row):
        if row is None or row.state != "open":
            return Outcome("This review request closed before you signed up.")
        if picked and not alongside and (row.reviewers or row.picks):
            return AlreadyClaimed(
                "This pull request already has a reviewer or pending pick. Stop assigning reviewers."
            )
        existing = row.participant(reviewer.user.id)
        if existing is not None and (picked or existing.decision not in ("picked", "expired")):
            return Outcome(f"@{reviewer.github_login} is already reviewing this.")
        if existing is not None:
            existing.decision = "review"
        else:
            row.participants.append(
                HumanReviewParticipant(
                    user_id=reviewer.user.id,
                    decision="picked" if picked else "review",
                    assigned_by_agent=picked,
                )
            )
    logger.info(
        "Added a human reviewer",
        extra={
            "request_id": str(request.id),
            "github_login": reviewer.github_login,
            "picked": picked,
            "accepted_pick": existing is not None,
        },
    )
    current = await HumanReviewRequest.get(request.id)
    if current is None:
        return Outcome("This review request vanished.")
    await ReviewCard(current).refresh()
    return current


async def claim(request: HumanReviewRequest, user: User | None) -> Outcome:
    """Someone signs up from the card, or accepts Open SWE's pick of them; any number may.

    Whoever else Open SWE picked and is still waiting on no longer needs to.
    """
    reviewer = await resolve_writer(request, user)
    if isinstance(reviewer, Outcome):
        return reviewer
    accepting = any(pick.user_id == reviewer.user.id for pick in request.picks)
    added = await _add_reviewer(request, reviewer, picked=False)
    if isinstance(added, Outcome):
        return added
    pr = added.pull_request
    label = f"<{pr.url}|{pr.owner}/{pr.repo}#{pr.number}>"
    if others := {pick.user_id for pick in added.picks}:
        await ReviewPicks(added).drop(
            others,
            f"{mention(reviewer.user)} is reviewing {label} *{escape(pr.title)}*, so you no "
            "longer need to. Open SWE removed you as a reviewer.",
        )
    if accepting:
        return Outcome(
            f"You accepted the review of {label}. It merges once every reviewer approves on GitHub."
        )
    requested = await _request_github_review(added, reviewer.github_login)
    note = "" if requested else " GitHub did not add you as a requested reviewer."
    return Outcome(
        f"You're down to review {label}. It merges once every reviewer approves on GitHub.{note}"
    )


async def snooze(request: HumanReviewRequest, user: User | None, length: str) -> Outcome:
    """Hold ``user``'s pending pick for ``length``, one of ``SNOOZE_DURATIONS``, then remind them."""
    if user is None:
        return Outcome("Link your Open SWE account before snoozing a review.")
    duration = SNOOZE_DURATIONS[length]
    until = datetime.now(UTC) + duration
    async with HumanReviewRequest.locked(request.id) as (_, row):
        participant = row.participant(user.id) if row else None
        if (
            row is None
            or row.state != "open"
            or participant is None
            or participant.decision != "picked"
        ):
            return Outcome("This reviewer pick is no longer pending for you.")
        participant.joined_at = until
        row.run_config = {**row.run_config, f"review_snoozed:{user.id}": until.isoformat()}
    await _schedule(
        request, "pick_expiry", duration + timedelta(minutes=await _assignment_minutes(request))
    )
    await _schedule(request, f"snooze:{user.id}", duration)
    return Outcome(f"Review snoozed for {length}; your pick stays reserved until then.")


async def decline(request: HumanReviewRequest, user: User | None, reason: str) -> Outcome:
    if user is None:
        return Outcome("Link your Open SWE account before declining a review.")
    if request.state != "open":
        return Outcome("This review request is no longer open.")
    dropped = await ReviewPicks(request).drop({user.id}, None, expired=True)
    if not dropped:
        return Outcome("This reviewer pick is no longer pending for you.")
    logger.info(
        "Reviewer declined a pending pick",
        extra={"request_id": str(request.id), "user_id": str(user.id), "reason": reason},
    )
    trigger = PickTrigger("declined", tuple(p.github_login for p in dropped), reason)
    await start_auto_assign(request, asked=True, trigger=trigger)
    return Outcome("Review declined; Open SWE will find another reviewer.")


async def _github_approvers(request: HumanReviewRequest) -> list[str]:
    """Who has approved the pull request on GitHub; empty when GitHub cannot be read."""
    pr = request.pull_request
    try:
        async with PullRequestClient.as_app(pr.owner, pr.repo, pr.number) as pull:
            states = await latest_review_states(pull, pr.author)
    except GitHubAppUnavailable:
        return []
    if states is None:
        logger.warning(
            "Could not read reviews before picking a reviewer",
            extra={"request_id": str(request.id)},
        )
        return []
    return [login for login, state in states.items() if state == "APPROVED"]


async def assign(
    request: HumanReviewRequest, github_login: str, reason: str, *, replace: bool = False
) -> RequestResult:
    """Pick ``github_login`` to review: show them on the card and DM them.

    The first reviewer stands alone. More join only for code owner areas nobody on the request
    owns, and unasked only once someone approved. ``replace`` is a person naming the reviewer:
    Open SWE's pending picks for the same code are withdrawn first.
    """
    approvers = await _github_approvers(request)
    coverage = await Coverage.load(request)
    if approvers and (coverage is None or not coverage.uncovered(approvers)):
        names = ", ".join(f"@{login}" for login in approvers)
        return _failure(
            f"{names} already approved this pull request on GitHub for all of its code "
            "owners, so it needs no reviewer. Do not pick anyone."
        )
    user = await User.for_login("github", github_login)
    if user is None:
        return _failure(f"@{github_login} is not an Open SWE user; pick someone who is.")
    if (previous := request.participant(user.id)) is not None and previous.decision == "expired":
        return _failure(f"@{github_login} already let this pick expire; pick someone else.")
    reviewer = await resolve_writer(request, user)
    if isinstance(reviewer, Outcome):
        return _failure(reviewer.message)
    pr = request.pull_request
    label = f"<{pr.url}|{pr.owner}/{pr.repo}#{pr.number}>"
    theirs = coverage.of(github_login) if coverage is not None else []
    if replace and (
        others := {
            p.user_id
            for p in request.picks
            if p.user_id != user.id
            and (coverage is None or not theirs or set(coverage.of(p.github_login)) & set(theirs))
        }
    ):
        await ReviewPicks(request).drop(
            others,
            f"Open SWE asked @{github_login} to review {label} *{escape(pr.title)}* instead, "
            "so you no longer need to.",
        )
        request = await HumanReviewRequest.get(request.id) or request
    assigned = [p.github_login for p in request.reviewers + request.picks]
    alongside = bool(assigned or approvers)
    if alongside and not replace:
        open_areas = coverage.uncovered([*assigned, *approvers]) if coverage is not None else []
        if not any(area in open_areas for area in theirs):
            return RequestResult(
                success=False,
                error=(
                    "This pull request already has a reviewer for the code "
                    f"@{github_login} owns. Do not pick anyone else for it."
                ),
                claimed=True,
            )
        if not approvers:
            return RequestResult(
                success=False,
                error=(
                    "Reviewers for the other code owners are picked only after the first "
                    "reviewer approves. Do not pick anyone else yet."
                ),
                claimed=True,
            )
    added = await _add_reviewer(request, reviewer, picked=True, alongside=alongside)
    if isinstance(added, Outcome):
        return RequestResult(
            success=False, error=added.message, claimed=isinstance(added, AlreadyClaimed)
        )
    if replace:
        async with HumanReviewRequest.locked(added.id) as (_, row):
            if row is not None:
                row.run_config = {**row.run_config, _AUTO_ASSIGN_ASKED: True}
    await _request_github_review(added, github_login)
    minutes = await _assignment_minutes(added)
    if not await _schedule(added, "pick_expiry", timedelta(minutes=minutes)):
        logger.warning(
            "A reviewer pick will not rotate if it is never accepted",
            extra={"request_id": str(added.id), "github_login": github_login},
        )
    why = f" {escape(reason.strip())}" if reason.strip() else ""
    deadline = f" Accept within {minutes} minutes, or Open SWE will ask someone else."
    accept = actions(accept_button(added), decline_button(added), snooze_button(added))
    permalink = await _permalink(added)
    if user.slack_user_id:
        where = "review card" if added.has_card else "Slack post"
        card = f" (<{permalink}|{where}>)" if permalink else ""
        dm_text = (
            f"Open SWE picked you to review {label} *{escape(pr.title)}*{card}.{why}{deadline}"
        )
        origin = added.dm_origin
        sent = await send_dm_with_location(
            user.slack_user_id,
            dm_text,
            blocks=block_payload(
                [
                    section(dm_text),
                    accept,
                    *await origin_footer(added.thread_id, origin.location if origin else None),
                ]
            ),
            origin=origin,
        )
        if sent is not None:
            await added.record_pick_message(
                user.id, PickMessage(channel_id=sent[0], ts=sent[1], text=dm_text)
            )
    await _schedule(added, f"remind:{user.id}", timedelta(0))
    return RequestResult(
        success=True,
        request_id=str(added.id),
        channel=added.slack_channel_id,
        permalink=permalink,
    )


class PendingPick(BaseModel):
    github_login: str
    accept_by: datetime | None
    snoozed_until: datetime | None


class AreaStatus(BaseModel):
    code_owners: list[str]
    changed_files: int
    approved_by: list[str]
    assigned: list[str]


class ReviewStatus(BaseModel):
    """Who an open review request is assigned to, for an agent asked about it."""

    requested_by: str
    reviewers: list[str]
    pending_picks: list[PendingPick]
    released: list[str]
    approved_by: list[str]
    code_owner_areas: list[AreaStatus]

    @classmethod
    async def of(cls, request: HumanReviewRequest) -> Self:
        wait = timedelta(minutes=await _assignment_minutes(request))
        now = datetime.now(UTC)
        picks: list[PendingPick] = []
        for pick in request.picks:
            snoozed = request.run_config.get(f"review_snoozed:{pick.user_id}")
            until = datetime.fromisoformat(snoozed) if isinstance(snoozed, str) else None
            picks.append(
                PendingPick(
                    github_login=pick.github_login,
                    accept_by=pick.joined_at + wait if pick.joined_at else None,
                    snoozed_until=until if until and until > now else None,
                )
            )
        requester = request.requested_by
        approvers = await _github_approvers(request)
        coverage = await Coverage.load(request)
        assigned = [p.github_login for p in request.reviewers + request.picks]
        return cls(
            requested_by=requester.login_for("github") if requester else "",
            reviewers=[p.github_login for p in request.reviewers],
            pending_picks=picks,
            released=[p.github_login for p in request.participants if p.decision == "expired"],
            approved_by=approvers,
            code_owner_areas=[
                AreaStatus(
                    code_owners=list(area.handles),
                    changed_files=len(area.files),
                    approved_by=[login for login in approvers if login.lower() in area.owners],
                    assigned=[login for login in assigned if login.lower() in area.owners],
                )
                for area in (coverage.areas if coverage is not None else ())
            ],
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


async def _settle_posted(
    request: HumanReviewRequest,
    pull: PullRequestClient,
    snapshot: PullRequestSnapshot,
    states: dict[str, str],
) -> None:
    """React once the pull request's owners approve; until then, time how long it has sat green."""
    approvers = {login for login, state in states.items() if state == "APPROVED"}
    approved = bool(approvers)
    if approved:
        pr = request.pull_request
        try:
            codeowners = await CodeOwners.fetch(pull.repo, pr.base_ref or None, strict=True)
        except RepoFileUnreadableError:
            logger.warning(
                "Cannot confirm codeowner approvals",
                extra={"request_id": str(request.id)},
                exc_info=True,
            )
            approved = False
            codeowners = None
        if codeowners is not None:
            files = await ChangedFile.of_pull(pull)
            approved = (
                files is not None
                and len(files) < MAX_FILES
                and await codeowners.approved_by([file.filename for file in files], approvers)
            )
    if approved:
        await ReviewCard(request).mark_approved()
        await ReviewPicks(request).release(
            ", ".join(f"@{login}" for login in sorted(approvers)) + " approved it"
        )
        return
    await _pick_remaining_owners(request, sorted(approvers))
    async with HumanReviewRequest.locked(request.id) as (_, row):
        if row is None or row.state != "open":
            return
        if row.approved_at is not None:
            row.approved_at = None
            await remove_slack_reaction(
                row.slack_channel_id, row.slack_message_ts, "white_check_mark"
            )
    now = datetime.now(UTC)
    async with HumanReviewRequest.locked(request.id) as (_, row):
        if row is None or row.state != "open":
            return
        previous = row.ready_since
        if not snapshot.green:
            row.ready_since = None
            return
        # A check that finished after the clock started was rerun unseen, so it restarts the clock.
        ready_since = (
            now if previous is None else max(previous, snapshot.checks_finished_at or previous)
        )
        if ready_since == previous:
            return
        row.ready_since = ready_since
    wait = max(
        ready_since + timedelta(minutes=await _assignment_minutes(request)) - now, timedelta(0)
    )
    if await _schedule(request, "unclaimed", wait):
        return
    # Without its deadline nothing would ever bump the post, so the next settle schedules it again.
    async with HumanReviewRequest.locked(request.id) as (_, row):
        if row is not None and row.ready_since == ready_since:
            row.ready_since = previous


async def _posted_deadline(request: HumanReviewRequest) -> str | None:
    """Why a posted request's deadline does not bump it yet; ``None`` when it should."""
    if not await settle(request):
        await _schedule(request, "unclaimed", _DEADLINE_RETRY)
        return "retrying"
    current = await HumanReviewRequest.get(request.id)
    if current is None or current.state != "open":
        return "closed"
    if current.approved_at is not None:
        return "approved"
    waited = timedelta(minutes=await _assignment_minutes(current)) - _SCHEDULER_EARLINESS
    if current.ready_since is None:
        return "not_ready"
    elapsed = datetime.now(UTC) - current.ready_since
    if elapsed < waited:
        await _schedule(current, "unclaimed", waited + _SCHEDULER_EARLINESS - elapsed)
        return "not_ready"
    return None


async def settle(request: HumanReviewRequest) -> bool:
    """Re-read the pull request and move the request on: react, update the card, or merge.

    ``False`` only when GitHub could not be read, so nothing is known to have changed.
    """
    if request.kind == "posted" and skip_on_preview("settle_posted"):
        return True
    if request.kind not in SETTLED_KINDS or request.state != "open":
        return True
    pr = request.pull_request
    try:
        async with PullRequestClient.as_app(pr.owner, pr.repo, pr.number) as pull:
            return await _settle(request, pull)
    except GitHubAppUnavailable:
        return False


async def _settle(request: HumanReviewRequest, pull: PullRequestClient) -> bool:
    readiness = await Readiness.assess(pull)
    if readiness is None:
        return False
    snapshot = readiness.snapshot
    if await User.for_login("github", snapshot.author) is None:
        await ReviewCard(request).retire("cancelled", "PR author has no Open SWE account")
        return True
    if snapshot.merged:
        await ReviewCard(request).mark_merged()
        return True
    if snapshot.state != "open":
        await ReviewCard(request).mark_closed()
        return True
    await ReviewCard(request).update_blocked_reactions(snapshot)
    states = await latest_review_states(pull, snapshot.author)
    if states is None:
        return False
    if request.kind == "posted":
        await _settle_posted(request, pull, snapshot, states)
        return True
    if approvers := [login for login, state in states.items() if state == "APPROVED"]:
        coverage = await Coverage.load(request)
        if coverage is not None and coverage.uncovered(approvers):
            await _pick_remaining_owners(request, approvers)
        else:
            picked = len(request.reviewers) + len(request.picks)
            request = await ReviewPicks(request).release(
                ", ".join(f"@{login}" for login in approvers) + " approved it"
            )
            # The unclaimed deadline already fired, so only a fresh one can pick again if the approval goes.
            if request.kind == "standard" and len(request.reviewers) + len(request.picks) < picked:
                await _schedule(
                    request, "unclaimed", timedelta(minutes=await _assignment_minutes(request))
                )
    waiting = merge_wait(
        [reviewer.github_login for reviewer in request.reviewers],
        request.created_at,
        states,
        datetime.now(UTC),
    )
    if waiting is None and readiness.blockers:
        waiting = "; ".join(readiness.blockers)
    if waiting is not None:
        await ReviewCard(await _set_detail(request, waiting)).refresh()
        return True
    async with HumanReviewRequest.locked(request.id) as (_, row):
        if row is None or row.state != "open":
            return True
        result = await merge_pull_request(
            row, snapshot.head_sha, snapshot.allowed_merge_methods, pull
        )
        if result.status != "merged":
            row.detail = result.message
    if result.status == "merged":
        await ReviewCard(request).mark_merged()
        return True
    logger.warning(
        "Auto-merge of a reviewed pull request was refused",
        extra={"request_id": str(request.id), "merge_status": result.status},
    )
    current = await HumanReviewRequest.get(request.id)
    if current is not None:
        await ReviewCard(current).refresh()
    return True


async def settle_pull_request(owner: str, repo: str, number: int) -> None:
    request = await HumanReviewRequest.active_for(owner, repo, number)
    if request is not None:
        await settle(request)


async def settle_repository(owner: str, repo: str) -> None:
    """Re-check every open standard or posted request in a repository, for events that name no PR."""
    for request in await HumanReviewRequest.open_in_repository(owner, repo, kinds=SETTLED_KINDS):
        await settle(request)


@dataclass(frozen=True, slots=True)
class AutoAssignResult:
    status: Literal["picked", "waiting", "woken", "failed", "disabled", "claimed"]
    reviewer: str = ""
    at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PickTrigger:
    """Background work that needs a reviewer picked; the Slack thread's agent decides."""

    kind: Literal["unclaimed", "expired", "declined", "approved"]
    logins: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class Suggestion:
    """Open SWE's choice for the picking agent, for one code owner area when ``area`` is set."""

    pick: Pick | None
    area: Area | None = None


async def start_auto_assign(
    request: HumanReviewRequest, *, asked: bool = False, trigger: PickTrigger | None = None
) -> AutoAssignResult:
    """Pick a reviewer, waking an agent when nobody qualifies.

    ``asked`` is someone requesting it now rather than the deadline passing. A
    ``trigger`` hands the pick to the agent owning the request's Slack thread,
    with Open SWE's choice as a suggestion.
    """
    if asked:
        async with HumanReviewRequest.locked(request.id) as (_, row):
            if row is not None:
                row.run_config = {**row.run_config, _AUTO_ASSIGN_ASKED: True}
    result = (
        AutoAssignResult("disabled")
        if not asked and skip_on_preview("start_auto_assign")
        else await _auto_assign(request, asked=asked, trigger=trigger)
    )
    logger.info(
        "Auto-assign finished",
        extra={
            "request_id": str(request.id),
            "asked": asked,
            "status": result.status,
            "github_login": result.reviewer,
            "until": result.at.isoformat() if result.at else "",
        },
    )
    return result


async def _auto_assign(
    request: HumanReviewRequest, *, asked: bool, trigger: PickTrigger | None
) -> AutoAssignResult:
    request = await HumanReviewRequest.get(request.id) or request
    # A decline or expiry leaves other code owners' reviewers in place; only that code needs one.
    replacing = trigger is not None and trigger.kind in ("declined", "expired")
    if not replacing and (request.reviewers or request.picks):
        return AutoAssignResult("claimed")
    if await User.for_login("github", request.pull_request.author) is None:
        await ReviewCard(request).retire("cancelled", "PR author has no Open SWE account")
        return AutoAssignResult("disabled")
    choice = await choose_reviewer(request)
    if isinstance(choice, Wait):
        if await _schedule(request, "unclaimed", choice.until - datetime.now(UTC)):
            return AutoAssignResult("waiting", choice.login, choice.until)
        return AutoAssignResult("failed")
    if trigger is not None:
        woken = await _wake_picker(
            request, asked=asked, trigger=trigger, suggestions=[Suggestion(choice)]
        )
        return AutoAssignResult("woken" if woken else "failed")
    if isinstance(choice, Pick):
        result = await assign(request, choice.login, choice.reason)
        if result.success:
            return AutoAssignResult("picked", choice.login)
        if result.claimed:
            return AutoAssignResult("claimed")
        logger.warning(
            "Open SWE's reviewer pick was refused; waking an agent to pick",
            extra={
                "request_id": str(request.id),
                "github_login": choice.login,
                "error": result.error,
            },
        )
    woken = await _wake_picker(request, asked=asked, trigger=None, suggestions=[])
    return AutoAssignResult("woken" if woken else "failed")


async def _pick_remaining_owners(request: HumanReviewRequest, approvers: list[str]) -> None:
    """After an approval, have the thread's agent pick one reviewer per code owner area left, at once."""
    if not approvers or _REMAINING_OWNERS_ASKED in request.run_config:
        return
    coverage = await Coverage.load(request)
    if coverage is None:
        return
    assigned = [p.github_login for p in request.reviewers + request.picks]
    if not (open_areas := coverage.uncovered([*approvers, *assigned])):
        return
    if await _auto_assign_hold(request, "unclaimed"):
        return
    async with HumanReviewRequest.locked(request.id) as (_, row):
        if row is None or row.state != "open" or _REMAINING_OWNERS_ASKED in row.run_config:
            return
        row.run_config = {**row.run_config, _REMAINING_OWNERS_ASKED: True}
    suggestions: list[Suggestion] = []
    for area in open_areas:
        choice = await choose_reviewer(request, area=area)
        suggestions.append(Suggestion(choice if isinstance(choice, Pick) else None, area))
    logger.info(
        "Asking for reviewers for the code owners an approval left",
        extra={
            "request_id": str(request.id),
            "approvers": approvers,
            "code_owner_areas": [list(area.handles) for area in open_areas],
        },
    )
    trigger = PickTrigger("approved", tuple(approvers))
    if not await _wake_picker(request, asked=False, trigger=trigger, suggestions=suggestions):
        async with HumanReviewRequest.locked(request.id) as (_, row):
            if row is not None:
                row.run_config = {
                    key: value
                    for key, value in row.run_config.items()
                    if key != _REMAINING_OWNERS_ASKED
                }


async def _wake_picker(
    request: HumanReviewRequest,
    *,
    asked: bool,
    trigger: PickTrigger | None,
    suggestions: list[Suggestion],
) -> bool:
    pr = request.pull_request
    text = prompt(
        "runs/human-review-unclaimed",
        pr_url=pr.url,
        minutes=await _assignment_minutes(request),
        author=pr.author,
        posted=not request.has_card,
        asked=asked,
        trigger=trigger,
        suggestions=suggestions,
    )
    requester = request.requested_by
    thread_ts = request.slack_thread_ts or request.slack_message_ts
    if requester is not None and requester.slack_user_id and request.slack_channel_id and thread_ts:
        try:
            await wake_thread_owner(
                request.slack_channel_id, thread_ts, requester.slack_user_id, text
            )
        except Exception:
            logger.warning(
                "Could not wake the Slack thread's agent to pick a reviewer; retrying",
                extra={"request_id": str(request.id), "slack_channel": request.slack_channel_id},
                exc_info=True,
            )
            await _schedule(request, "unclaimed", _DEADLINE_RETRY)
            return False
        return True
    if request.thread_id:
        return await ReviewCard(request).notify_agent(text)
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


async def _remind_reviewer(request: HumanReviewRequest, user_id: str) -> str:
    try:
        participant = request.participant(UUID(user_id))
    except ValueError:
        return "invalid_reviewer"
    if (
        participant is None
        or participant.decision not in ("picked", "review")
        or not participant.assigned_by_agent
        or participant.joined_at is None
        or not participant.user.slack_user_id
    ):
        return "not_assigned"
    marker = f"review_reminded:{user_id}:{participant.joined_at.isoformat()}"
    if request.run_config.get(marker):
        return "already_reminded"
    info = await get_slack_user_info(participant.user.slack_user_id)
    timezone_name = info.get("tz") if info else None
    try:
        timezone = ZoneInfo(timezone_name) if isinstance(timezone_name, str) else None
    except ZoneInfoNotFoundError:
        timezone = None
    if timezone is None:
        logger.warning(
            "Reviewer timezone unavailable",
            extra={"request_id": str(request.id), "user_id": user_id},
        )
        await _schedule(request, f"remind:{user_id}", _DEADLINE_RETRY)
        return "retrying"
    now = datetime.now(UTC)
    due = review_reminder_at(participant.joined_at, timezone)
    local_now = now.astimezone(timezone)
    if now >= due and (local_now.weekday() >= 5 or not time(9) <= local_now.time() < time(18)):
        due = review_reminder_at(now, timezone) - timedelta(hours=2)
    remaining = due - now
    if remaining > timedelta(0):
        await _schedule(request, f"remind:{user_id}", remaining)
        return "scheduled"
    pr = request.pull_request
    try:
        async with PullRequestClient.as_app(pr.owner, pr.repo, pr.number) as pull:
            details = await or_none(pull.pull())
            authors = await review_authors(pull) if details is not None else None
    except GitHubAppUnavailable:
        details = authors = None
    if details is None:
        await _schedule(request, f"remind:{user_id}", _DEADLINE_RETRY)
        return "retrying"
    if details.get("state") != "open" or details.get("merged"):
        return "closed"
    if authors is None:
        await _schedule(request, f"remind:{user_id}", _DEADLINE_RETRY)
        return "retrying"
    if participant.github_login.lower() in authors:
        return "reviewed"
    async with HumanReviewRequest.locked(request.id) as (_, row):
        current = row.participant(participant.user_id) if row else None
        if (
            row is None
            or row.state != "open"
            or current is None
            or not current.assigned_by_agent
            or current.decision not in ("picked", "review")
            or current.joined_at != participant.joined_at
            or row.run_config.get(marker)
        ):
            return "inactive"
        waited_minutes = max(
            0,
            int(
                (datetime.now(UTC) - (request.created_at or participant.joined_at)).total_seconds()
                // 60
            ),
        )
        days, minutes = divmod(waited_minutes, 1440)
        hours, minutes = divmod(minutes, 60)
        waited = (
            ", ".join(
                f"{value} {unit}{'s' if value != 1 else ''}"
                for value, unit in ((days, "day"), (hours, "hour"), (minutes, "minute"))
                if value
            )
            or "less than a minute"
        )
        sent = await send_dm(
            participant.user.slack_user_id,
            f"Reminder: Open SWE picked you to review <{pr.url}|{pr.owner}/{pr.repo}#{pr.number}> "
            f"*{escape(pr.title)}*. {mention(request.requested_by) if request.requested_by else 'The author'} "
            f"has been waiting {waited} since the review request was opened. "
            "Please submit your review on GitHub.",
            origin=request.dm_origin,
        )
        if sent:
            row.run_config = {**row.run_config, marker: True}
    if not sent:
        await _schedule(request, f"remind:{user_id}", _DEADLINE_RETRY)
        return "retrying"
    return "reminded"


async def expire_picks(request: HumanReviewRequest) -> str:
    """Rotate away from picks not accepted in time, when someone else could review instead.

    Reviewing on GitHub counts as accepting.
    """
    minutes = await _assignment_minutes(request)
    wait = timedelta(minutes=minutes) - _SCHEDULER_EARLINESS
    now = datetime.now(UTC)
    stale: list[HumanReviewParticipant] = []
    for pick in request.picks:
        snoozed = request.run_config.get(f"review_snoozed:{pick.user_id}")
        if isinstance(snoozed, str) and (until := datetime.fromisoformat(snoozed)) > now:
            await _schedule(request, "pick_expiry", until - now)
            continue
        if pick.joined_at is None or now - pick.joined_at >= wait:
            stale.append(pick)
    logger.info(
        "Checking reviewer picks for expiry",
        extra={
            "request_id": str(request.id),
            "pending": [p.github_login for p in request.picks],
            "expired": [p.github_login for p in stale],
        },
    )
    if not stale:
        return "accepted"
    pr = request.pull_request
    try:
        async with PullRequestClient.as_app(pr.owner, pr.repo, pr.number) as pull:
            reviewed = await review_authors(pull)
    except GitHubAppUnavailable:
        reviewed = None
    if reviewed is None:
        await _schedule(request, "pick_expiry", _DEADLINE_RETRY)
        return "retrying"
    if started := [p for p in stale if p.github_login.lower() in reviewed]:
        logger.info(
            "A GitHub review counts as accepting the pick",
            extra={
                "request_id": str(request.id),
                "github_logins": [p.github_login for p in started],
            },
        )
        async with HumanReviewRequest.locked(request.id) as (_, row):
            for pick in row.picks if row is not None else []:
                if pick.user_id in {p.user_id for p in started}:
                    pick.decision = "review"
        request = await HumanReviewRequest.get(request.id) or request
        await ReviewCard(request).refresh()
    idle = [p for p in stale if p.github_login.lower() not in reviewed]
    if not idle:
        return "accepted"
    area = await _area_left_by(request, {p.github_login.lower() for p in idle})
    choice = await choose_reviewer(request, area=area)
    if choice is None:
        logger.info(
            "Nobody else can review, so an unaccepted pick stays",
            extra={"request_id": str(request.id)},
        )
        return "no_alternative"
    logger.info(
        "Rotating away from unaccepted reviewer picks",
        extra={
            "request_id": str(request.id),
            "expired": [p.github_login for p in idle],
            "next_github_login": choice.login,
            "next_waits_until": choice.until.isoformat() if isinstance(choice, Wait) else "",
        },
    )
    label = f"<{pr.url}|{pr.owner}/{pr.repo}#{pr.number}>"
    await ReviewPicks(request).drop(
        {p.user_id for p in idle},
        f"You didn't accept the review of {label} *{escape(pr.title)}* within "
        f"{minutes} minutes, so Open SWE released you from it.",
        expired=True,
    )
    current = await HumanReviewRequest.get(request.id)
    if current is None or current.state != "open":
        return "closed"
    if isinstance(choice, Wait):
        await _schedule(current, "unclaimed", choice.until - datetime.now(UTC))
        return "rotating"
    trigger = PickTrigger("expired", tuple(p.github_login for p in idle))
    suggestions = [Suggestion(choice, area)]
    if not await _wake_picker(current, asked=False, trigger=trigger, suggestions=suggestions):
        return "rotation_failed"
    return "rotating"


async def _area_left_by(request: HumanReviewRequest, leaving: set[str]) -> Area | None:
    """The code owner area ``leaving`` covered that nobody else on the request does."""
    coverage = await Coverage.load(request)
    if coverage is None:
        return None
    staying = [
        p.github_login
        for p in request.participants
        if p.decision != "expired" and p.github_login.lower() not in leaving
    ]
    return next((area for area in coverage.uncovered(staying) if area.owners & leaving), None)


async def _auto_assign_hold(request: HumanReviewRequest, step: str) -> str | None:
    """Why Open SWE does not pick reviewers unasked for ``request`` now; ``None`` if it may.

    A pull request merely linked outside a review channel only gets reactions, so
    its unaccepted picks are withdrawn.
    """
    if request.kind != "posted" or _AUTO_ASSIGN_ASKED in request.run_config:
        return None
    pr = request.pull_request
    try:
        async with GitHubClient.as_app(pr.owner, pr.repo) as github:
            if await in_review_channel(github.repo(pr.owner, pr.repo), request.slack_channel_id):
                return None
    except GitHubAppUnavailable:
        return None
    except ReviewChannelUnknownError:
        logger.warning(
            "Could not tell whether a posted pull request is in a review channel",
            extra={"request_id": str(request.id), "slack_channel": request.slack_channel_id},
            exc_info=True,
        )
        await _schedule(request, step, _DEADLINE_RETRY)
        return "retrying"
    logger.info(
        "Not auto-assigning a pull request posted outside its review channels",
        extra={"request_id": str(request.id), "slack_channel": request.slack_channel_id},
    )
    await ReviewPicks(request).drop(
        {pick.user_id for pick in request.picks},
        f"You no longer need to review <{pr.url}|{pr.owner}/{pr.repo}#{pr.number}> "
        f"*{escape(pr.title)}*: nobody asked Open SWE to find a reviewer for it.",
    )
    return "not_asked"


async def run_deadline(request_id: str, step: str) -> dict[str, str]:
    """Scheduler entry point for the unclaimed, pick-expiry and auto-merge deadlines."""
    try:
        request = await HumanReviewRequest.get(UUID(request_id))
    except ValueError:
        request = None
    if request is None or request.state != "open":
        return {"status": "closed"}
    if step.startswith("snooze:"):
        user_id = UUID(step.removeprefix("snooze:"))
        participant = request.participant(user_id)
        snoozed = request.run_config.get(f"review_snoozed:{user_id}")
        if participant is None or participant.decision != "picked" or not isinstance(snoozed, str):
            return {"status": "inactive"}
        remaining = datetime.fromisoformat(snoozed) - datetime.now(UTC)
        if remaining > timedelta(0):
            await _schedule(request, step, remaining)
            return {"status": "snoozed"}
        if participant.user.slack_user_id:
            text = f"Your review snooze ended: {request.pull_request.url}."
            origin = request.dm_origin
            sent = await send_dm_with_location(
                participant.user.slack_user_id,
                text,
                blocks=block_payload(
                    [
                        section(text),
                        actions(
                            accept_button(request), decline_button(request), snooze_button(request)
                        ),
                        *await origin_footer(
                            request.thread_id, origin.location if origin else None
                        ),
                    ]
                ),
                origin=origin,
            )
            if sent is not None:
                await request.record_pick_message(
                    user_id, PickMessage(channel_id=sent[0], ts=sent[1], text=text)
                )
        return {"status": "reminded"}
    if step.startswith("remind:"):
        return {"status": await _remind_reviewer(request, step.removeprefix("remind:"))}

    if (request.kind == "posted" or step in ("unclaimed", "pick_expiry")) and skip_on_preview(
        "run_deadline"
    ):
        return {"status": "disabled_in_preview"}
    if step in ("unclaimed", "pick_expiry") and (hold := await _auto_assign_hold(request, step)):
        return {"status": hold}
    if step == "pick_expiry":
        return {"status": await expire_picks(request)}
    if step == "unclaimed":
        if request.reviewers or request.picks:
            return {"status": "claimed"}
        if request.kind == "posted" and (waiting := await _posted_deadline(request)) is not None:
            return {"status": waiting}
        if request.kind == "standard" and request.created_at:
            remaining = (
                request.created_at
                + timedelta(minutes=await _assignment_minutes(request))
                - datetime.now(UTC)
            )
            if remaining > _SCHEDULER_EARLINESS:
                await _schedule(request, "unclaimed", remaining)
                return {"status": "waiting"}
        if request.kind == "standard" and await _github_approvers(request):
            # Re-checked later in case the approval is dismissed while the request stays open.
            await _schedule(
                request, "unclaimed", timedelta(minutes=await _assignment_minutes(request))
            )
            return {"status": "approved"}
        return {
            "status": (await start_auto_assign(request, trigger=PickTrigger("unclaimed"))).status
        }
    if step == "auto_merge":
        await settle(request)
        return {"status": "settled"}
    return {"status": "unknown_step"}
