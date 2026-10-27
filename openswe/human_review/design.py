"""Supplementary Product Design review, independent of engineering reviewer signup."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath

from pydantic import BaseModel

from openswe.dashboard.profiles import PROFILES
from openswe.expedited_review.eligibility import ChangedFile
from openswe.expedited_review.readiness import latest_review_states
from openswe.github.org_membership import team_members
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequestPayload
from openswe.github.repo_files import DesignReviewSettings, RepoSettings
from openswe.human_review.deadlines import schedule
from openswe.human_review.picking import Coverage, ReviewerAvailability
from openswe.human_review.requests import HumanReviewRequest, slack_mention
from openswe.prompts import prompt
from openswe.slack.client import post_slack_thread_reply_with_ts
from openswe.slack.dm import send_dm
from openswe.threads.runs import start_dashboard_thread
from openswe.users import User
from openswe.utils.preview import skip_on_preview

logger = logging.getLogger(__name__)
_KEY = "design_review"
_RETRY = timedelta(hours=1)


class DesignState(BaseModel):
    assessment_requested: bool = False
    assessment_started_at: datetime | None = None
    reason: str = ""
    github_login: str = ""
    assigned_at: datetime | None = None
    github_requested: bool = False
    github_notified: bool = False
    slack_notified: bool = False
    dm_notified: bool = False


def potential_ui(path: str, settings: DesignReviewSettings) -> bool:
    """Production frontend paths warrant judgment, not automatic design assignment."""
    file = PurePosixPath(path.lower())
    return (
        settings.matches(path)
        and file.suffix in {".tsx", ".jsx", ".ts", ".js", ".css", ".scss", ".sass", ".less"}
        and not {"tests", "test", "__tests__", "e2e", "playwright", "cypress", "__mocks__"}
        & set(file.parts)
        and not any(part in file.name for part in (".test.", ".spec.", ".stories.", ".d.ts"))
    )


@dataclass
class DesignReview:
    request: HumanReviewRequest

    async def settings(self) -> DesignReviewSettings | None:
        if self.request.kind not in ("standard", "posted"):
            return None
        pr = self.request.pull_request
        return (await RepoSettings.cached(pr.owner, pr.repo)).design_review

    @property
    def state(self) -> DesignState:
        return DesignState.model_validate(self.request.run_config.get(_KEY) or {})

    async def _schedule(self, after: timedelta = timedelta(0)) -> None:
        if not await schedule(self.request, "design_review", after):
            raise RuntimeError("Could not schedule design review")

    async def start(self, pull: PullRequestClient | None = None, reason: str = "") -> None:
        try:
            if pull is not None:
                await self._start(pull, reason)
            elif await self.settings() is not None:
                pr = self.request.pull_request
                async with PullRequestClient.as_app(pr.owner, pr.repo, pr.number) as client:
                    await self._start(client, reason)
        except Exception:
            logger.exception(
                "Could not start supplementary design review",
                extra={"request_id": str(self.request.id)},
            )

    async def _start(self, pull: PullRequestClient, reason: str) -> None:
        """Queue immediate semantic assessment, or an agent's reasoned design-review request."""
        settings = await self.settings()
        if settings is None or self.request.state != "open":
            return
        if not reason and self.state.assessment_requested:
            return
        files = await ChangedFile.of_pull(pull)
        if files is None or not any(potential_ui(file.filename, settings) for file in files):
            return
        async with HumanReviewRequest.locked(self.request.id) as (_, row):
            if row is None or row.state != "open":
                return
            state = DesignReview(row).state
            if reason and not state.reason:
                state.reason = reason.strip()
                row.run_config = {**row.run_config, _KEY: state.model_dump(mode="json")}
        await self._schedule()

    async def run(self) -> str:
        """Assess once, then rotate a designer as soon as someone eligible is working."""
        if await self.settings() is None or skip_on_preview("design_review"):
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
                    "Could not deliver a design review request",
                    extra={"request_id": str(self.request.id)},
                )
                await self._schedule(_RETRY)
                return "retrying"

    async def _assess(self) -> str:
        wait = timedelta(0)
        async with HumanReviewRequest.locked(self.request.id) as (_, row):
            if row is None or row.state != "open":
                return "closed"
            state = DesignReview(row).state
            if state.assessment_requested:
                return "assessed"
            now = datetime.now(UTC)
            if state.assessment_started_at is not None:
                wait = state.assessment_started_at + _RETRY - now
            if wait <= timedelta(0):
                state.assessment_started_at = now
                row.run_config = {**row.run_config, _KEY: state.model_dump(mode="json")}
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
                        prompt=prompt("runs/design-review", pr_url=pr.url),
                        title=f"Assess design review for {pr.repo}#{pr.number}",
                        repos=[f"{pr.owner}/{pr.repo}"],
                        visibility="private",
                        unlisted=True,
                    )
                )
        except Exception:
            logger.exception(
                "Could not request design review assessment",
                extra={"request_id": str(self.request.id)},
            )
        finally:
            async with HumanReviewRequest.locked(self.request.id) as (_, row):
                if row is not None:
                    state = DesignReview(row).state
                    if state.assessment_started_at == now:
                        state.assessment_requested = dispatched
                        state.assessment_started_at = None
                        row.run_config = {**row.run_config, _KEY: state.model_dump(mode="json")}
        if dispatched:
            return "assessment_requested"
        await self._schedule(_RETRY)
        return "retrying"

    async def _candidates(self, pull: PullRequestClient) -> list[tuple[User, ReviewerAvailability]]:
        settings = await self.settings()
        if settings is None:
            return []
        org, team = settings.team.split("/", 1)
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
            async with self.request.design_rotation() as (row, history):
                if row is None or row.state != "open":
                    return "closed"
                state = DesignReview(row).state
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
                        row.run_config = {**row.run_config, _KEY: state.model_dump(mode="json")}
            if delay > timedelta(0):
                await self._schedule(delay)
                return "waiting"
        async with HumanReviewRequest.locked(self.request.id) as (_, row):
            if row is None or row.state != "open":
                return "closed"
            state = DesignReview(row).state
            user = await User.for_login("github", state.github_login)
            pr = row.pull_request
            note = (
                f"Design review — {slack_mention(user, state.github_login)}: {state.reason}\n"
                f"<{pr.url}|{pr.repo}#{pr.number}> · Supplementary UX review; engineering review remains separate."
            )
            failed = False
            try:
                if not state.github_requested:
                    await pull.request_reviewers([state.github_login])
                    state.github_requested = True
                if not state.github_notified:
                    await pull.comment(
                        f"### Design review\n\n@{state.github_login} — {state.reason}\n\n"
                        "Supplementary Product Design review; this does not replace engineering or CODEOWNERS review."
                    )
                    state.github_notified = True
                if not state.slack_notified:
                    state.slack_notified = bool(
                        await post_slack_thread_reply_with_ts(
                            row.slack_channel_id,
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
                    "Could not deliver design review notifications",
                    extra={"request_id": str(row.id)},
                )
                failed = True
            row.run_config = {**row.run_config, _KEY: state.model_dump(mode="json")}
        if failed or not state.dm_notified or not state.slack_notified:
            await self._schedule(_RETRY)
            return "retrying"
        return "requested"

    async def engineering_states(self, states: dict[str, str]) -> dict[str, str]:
        """A supplementary approval is not an engineering approval unless they also own or signed up."""
        designer = self.state.github_login.lower()
        if not designer or not any(
            login.lower() == designer and state == "APPROVED" for login, state in states.items()
        ):
            return states
        if any(
            p.github_login.lower() == designer for p in self.request.reviewers + self.request.picks
        ):
            return states
        coverage = await Coverage.load(self.request)
        if coverage is not None and coverage.of(designer):
            return states
        return {
            login: state
            for login, state in states.items()
            if login.lower() != designer or state != "APPROVED"
        }
