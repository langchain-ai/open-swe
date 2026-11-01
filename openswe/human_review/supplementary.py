"""Supplementary reviews: specialist reviewers a repository configures alongside engineering review.

Each policy in ``supplementaryReviews`` (``.open-swe/settings.json``) names a GitHub team, the
paths it cares about, and the criteria an isolated assessment judges the pull request by. When a
pull request qualifies, a member of that team is rotated in and notified. Their approval never
stands in for engineering review.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from openswe.dashboard.profiles import PROFILES
from openswe.expedited_review.eligibility import ChangedFile
from openswe.expedited_review.readiness import latest_review_states
from openswe.github.org_membership import team_members
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequestPayload
from openswe.github.repo_files import RepoSettings, SupplementaryReviewPolicy
from openswe.human_review.deadlines import schedule
from openswe.human_review.picking import Coverage, ReviewerAvailability
from openswe.human_review.requests import (
    SUPPLEMENTARY_REVIEWS_KEY,
    HumanReviewRequest,
    slack_mention,
)
from openswe.prompts import prompt
from openswe.slack.dm import send_dm
from openswe.threads.runs import start_dashboard_thread
from openswe.users import User
from openswe.utils.preview import skip_on_preview

logger = logging.getLogger(__name__)
STEP_PREFIX = "supplementary_review:"
_RETRY = timedelta(hours=1)


class SupplementaryReviewRefused(Exception):
    """A requested supplementary review this pull request cannot have."""


class SupplementaryState(BaseModel):
    assessment_requested: bool = False
    assessment_started_at: datetime | None = None
    reason: str = ""
    github_login: str = ""
    assigned_at: datetime | None = None
    github_requested: bool = False
    github_notified: bool = False
    slack_notified: bool = False
    dm_notified: bool = False


def _stored(request: HumanReviewRequest) -> dict[str, SupplementaryState]:
    raw = request.run_config.get(SUPPLEMENTARY_REVIEWS_KEY)
    if not isinstance(raw, dict):
        return {}
    return {key: SupplementaryState.model_validate(value) for key, value in raw.items()}


@dataclass
class SupplementaryReview:
    """One configured policy applied to one review request."""

    request: HumanReviewRequest
    policy: SupplementaryReviewPolicy

    @property
    def step(self) -> str:
        return f"{STEP_PREFIX}{self.policy.id}"

    @property
    def state(self) -> SupplementaryState:
        return self.state_of(self.request)

    def state_of(self, row: HumanReviewRequest) -> SupplementaryState:
        return _stored(row).get(self.policy.id) or SupplementaryState()

    def save(self, row: HumanReviewRequest, state: SupplementaryState) -> None:
        stored = row.run_config.get(SUPPLEMENTARY_REVIEWS_KEY)
        reviews = dict(stored) if isinstance(stored, dict) else {}
        reviews[self.policy.id] = state.model_dump(mode="json")
        row.run_config = {**row.run_config, SUPPLEMENTARY_REVIEWS_KEY: reviews}

    def applies_to(self, files: list[ChangedFile]) -> bool:
        return any(self.policy.matches(file.filename) for file in files)

    async def _schedule(self, after: timedelta = timedelta(0)) -> None:
        if not await schedule(self.request, self.step, after):
            raise RuntimeError("Could not schedule a supplementary review")

    async def start(self, reason: str = "") -> None:
        """Queue the assessment, or record an agent's reason that the review is needed."""
        if self.request.state != "open":
            return
        if not reason and self.state.assessment_requested:
            return
        async with HumanReviewRequest.locked(self.request.id) as (_, row):
            if row is None or row.state != "open":
                return
            state = self.state_of(row)
            if reason and not state.reason:
                state.reason = reason.strip()
                self.save(row, state)
        await self._schedule()

    async def run(self) -> str:
        """Assess once, then rotate a reviewer in as soon as someone eligible is working."""
        if skip_on_preview("supplementary_review"):
            return "disabled"
        pr = self.request.pull_request
        async with PullRequestClient.as_app(pr.owner, pr.repo, pr.number) as pull:
            details = PullRequestPayload.model_validate(await pull.pull())
            if details.merged or details.state != "open":
                return "not_ready"
            if details.draft:
                await self._schedule(_RETRY)
                return "not_ready"
            if not self.state.reason:
                return await self._assess()
            try:
                return await self._assign(pull)
            except Exception:
                logger.exception(
                    "Could not deliver a supplementary review request",
                    extra={"request_id": str(self.request.id), "policy": self.policy.id},
                )
                await self._schedule(_RETRY)
                return "retrying"

    async def _assess(self) -> str:
        wait = timedelta(0)
        async with HumanReviewRequest.locked(self.request.id) as (_, row):
            if row is None or row.state != "open":
                return "closed"
            state = self.state_of(row)
            if state.assessment_requested:
                return "assessed"
            now = datetime.now(UTC)
            if state.assessment_started_at is not None:
                wait = state.assessment_started_at + _RETRY - now
            if wait <= timedelta(0):
                state.assessment_started_at = now
                self.save(row, state)
        if wait > timedelta(0):
            await self._schedule(wait)
            return "assessing"
        pr = self.request.pull_request
        dispatched = False
        try:
            login = self.request.requester_login
            if login:
                dispatched = bool(
                    await start_dashboard_thread(
                        login,
                        None,
                        prompt=prompt(
                            "runs/supplementary-review",
                            pr_url=pr.url,
                            policy_id=self.policy.id,
                            label=self.policy.label,
                            criteria=self.policy.criteria,
                        ),
                        title=f"Assess {self.policy.label} for {pr.repo}#{pr.number}",
                        repos=[f"{pr.owner}/{pr.repo}"],
                        visibility="private",
                        unlisted=True,
                    )
                )
        except Exception:
            logger.exception(
                "Could not request a supplementary review assessment",
                extra={"request_id": str(self.request.id), "policy": self.policy.id},
            )
        finally:
            async with HumanReviewRequest.locked(self.request.id) as (_, row):
                if row is not None:
                    state = self.state_of(row)
                    if state.assessment_started_at == now:
                        state.assessment_requested = dispatched
                        state.assessment_started_at = None
                        self.save(row, state)
        if dispatched:
            return "assessment_requested"
        await self._schedule(_RETRY)
        return "retrying"

    async def _candidates(self, pull: PullRequestClient) -> list[tuple[User, ReviewerAvailability]]:
        org, team = self.policy.team.split("/", 1)
        members = await team_members(org, team)
        users = await asyncio.gather(*(User.for_login("github", login) for login in members or []))
        reviews = await latest_review_states(pull, self.request.pull_request.author)
        if reviews is None:
            return []
        already_reviewed = {login.lower() for login in reviews}
        eligible: list[tuple[User, ReviewerAvailability]] = []
        for user in users:
            if (
                user is None
                or not user.slack_user_id
                or self.request.is_author(user.id, user.github_login)
                or self.request.participant(user.id) is not None
                or user.github_login.lower() in already_reviewed
                or not await PROFILES.allows_auto_human_review(user.github_login)
            ):
                continue
            hours = await ReviewerAvailability.for_user(user)
            if hours.out_of_office is not False or not await pull.repo.can_write(user.github_login):
                continue
            eligible.append((user, hours))
        return eligible

    async def _assign(self, pull: PullRequestClient) -> str:
        if not self.state.github_login:
            candidates = await self._candidates(pull)
            if not candidates:
                await self._schedule(_RETRY)
                return "waiting"
            delay = timedelta(0)
            async with self.request.supplementary_rotation(self.policy.id) as (row, history):
                if row is None or row.state != "open":
                    return "closed"
                state = self.state_of(row)
                if not state.github_login:
                    now = datetime.now(UTC)
                    user, hours = min(
                        candidates,
                        key=lambda person: (
                            person[1].next_start(now),
                            history.get(
                                person[0].github_login.lower(), datetime.min.replace(tzinfo=UTC)
                            ),
                            person[0].github_login.lower(),
                        ),
                    )
                    delay = hours.next_start(now) - now
                    if delay <= timedelta(0):
                        state.github_login = user.github_login
                        state.assigned_at = now
                        self.save(row, state)
            if delay > timedelta(0):
                await self._schedule(delay)
                return "waiting"
        label = self.policy.label
        async with HumanReviewRequest.locked(self.request.id) as (_, row):
            if row is None or row.state != "open":
                return "closed"
            state = self.state_of(row)
            user = await User.for_login("github", state.github_login)
            pr = row.pull_request
            note = (
                f"{label} — {slack_mention(user, state.github_login)}: {state.reason}\n"
                f"<{pr.url}|{pr.repo}#{pr.number}> · Supplementary review; "
                "engineering review remains separate."
            )
            failed = False
            try:
                if not state.github_requested:
                    await pull.request_reviewers([state.github_login])
                    state.github_requested = True
                if not state.github_notified:
                    await pull.comment(
                        f"### {label}\n\n@{state.github_login} — {state.reason}\n\n"
                        "Supplementary review; this does not replace engineering or CODEOWNERS review."
                    )
                    state.github_notified = True
                if not state.slack_notified:
                    state.slack_notified = bool(
                        await row.slack_conversation.reply(
                            row.slack_thread_ts or row.slack_message_ts,
                            note,
                            unfurl_links=False,
                            unfurl_media=False,
                        )
                    )
                if not state.dm_notified and user is not None and user.slack_user_id:
                    state.dm_notified = await send_dm(
                        user.slack_user_id, note, origin=row.dm_origin
                    )
            except Exception:
                logger.exception(
                    "Could not deliver supplementary review notifications",
                    extra={"request_id": str(row.id), "policy": self.policy.id},
                )
                failed = True
            self.save(row, state)
        if failed or not state.dm_notified or not state.slack_notified:
            await self._schedule(_RETRY)
            return "retrying"
        return "requested"


@dataclass
class SupplementaryReviews:
    """Every supplementary review a review request's repository configures."""

    request: HumanReviewRequest

    async def policies(self) -> list[SupplementaryReviewPolicy]:
        if self.request.kind not in ("standard", "posted"):
            return []
        pr = self.request.pull_request
        return (await RepoSettings.cached(pr.owner, pr.repo)).supplementary_reviews

    async def start(self, pull: PullRequestClient | None = None) -> None:
        """Queue an assessment for each policy whose paths the pull request touches."""
        try:
            if not await self.policies():
                return
            if pull is not None:
                await self._start(pull)
                return
            pr = self.request.pull_request
            async with PullRequestClient.as_app(pr.owner, pr.repo, pr.number) as client:
                await self._start(client)
        except Exception:
            logger.exception(
                "Could not start supplementary reviews",
                extra={"request_id": str(self.request.id)},
            )

    async def _start(self, pull: PullRequestClient) -> None:
        files = await ChangedFile.of_pull(pull)
        if not files:
            return
        for policy in await self.policies():
            review = SupplementaryReview(self.request, policy)
            if review.applies_to(files):
                await review.start()

    async def ask(self, pull: PullRequestClient, policy_id: str, reason: str) -> None:
        """Ask for one policy's review with an agent's reason; raises when it cannot apply."""
        policies = await self.policies()
        policy = next((policy for policy in policies if policy.id == policy_id), None)
        if policy is None:
            configured = ", ".join(f"`{policy.id}`" for policy in policies) or "none"
            raise SupplementaryReviewRefused(
                f"This repository configures no `{policy_id}` supplementary review "
                f"(configured: {configured})."
            )
        review = SupplementaryReview(self.request, policy)
        files = await ChangedFile.of_pull(pull)
        if files is None:
            raise SupplementaryReviewRefused("GitHub was unavailable while reading changed files.")
        if not review.applies_to(files):
            raise SupplementaryReviewRefused(
                f"None of the pull request's changed files match the {policy.label} paths."
            )
        try:
            await review.start(reason)
        except RuntimeError:
            logger.exception(
                "Could not schedule a requested supplementary review",
                extra={"request_id": str(self.request.id), "policy": policy_id},
            )

    async def run(self, step: str) -> str:
        policy_id = step.removeprefix(STEP_PREFIX)
        policy = next((p for p in await self.policies() if p.id == policy_id), None)
        if policy is None:
            return "disabled"
        return await SupplementaryReview(self.request, policy).run()

    async def engineering_states(self, states: dict[str, str]) -> dict[str, str]:
        """Drop approvals from supplementary reviewers who are not also owners or engineering reviewers."""
        supplementary = {
            state.github_login.lower()
            for state in _stored(self.request).values()
            if state.github_login
        }
        approving = {
            login.lower()
            for login, state in states.items()
            if state == "APPROVED" and login.lower() in supplementary
        }
        if not approving:
            return states
        engineering = {p.github_login.lower() for p in self.request.reviewers + self.request.picks}
        coverage = await Coverage.load(self.request)
        ignored = {
            login for login in approving - engineering if coverage is None or not coverage.of(login)
        }
        return {
            login: state
            for login, state in states.items()
            if login.lower() not in ignored or state != "APPROVED"
        }
