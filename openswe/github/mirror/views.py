"""What the dashboard shows of pull requests, answered from the mirror.

Every read here is a handful of indexed queries however many pull requests it
covers; none waits on GitHub, except for the required checks of a blocked
branch, which only the branch's protection knows and which change rarely.
"""

import asyncio
import logging
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal, Self, TypedDict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import ColumnElement, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONPATH

from openswe.database import postgres
from openswe.github.ci import RequiredCheck
from openswe.github.http import GitHubAppUnavailable, GitHubClient
from openswe.github.mirror.ci import CheckState, HeadChecks
from openswe.github.mirror.conversation import Review, ReviewComment, ReviewThread
from openswe.github.org_membership import team_members
from openswe.github.pull_requests import PullRequest
from openswe.github.repositories import Repository
from openswe.users import User
from openswe.utils import ttl_cache

logger = logging.getLogger(__name__)

PAGE_SIZE: Final = 100
_OPEN_STATES: Final = ("open", "draft")
_REQUIRED_CHECKS_TTL: Final = 600.0
_TEAM_MEMBERS_TTL: Final = 600.0

ReviewDecision = Literal["approved", "changes_requested", "none"]
ReviewerState = Literal["approved", "changes_requested", "dismissed", "commented"]
PullRequestState = Literal["open", "closed", "merged"]
SidebarState = Literal["open", "draft", "merged", "closed"]


def avatar_url(github_id: int | None) -> str | None:
    return None if github_id is None else f"https://avatars.githubusercontent.com/u/{github_id}?v=4"


class PullRequestReviewer(BaseModel):
    """Someone who reviewed the pull request, and where their review stands.

    A comment never overrides an earlier approval or change request, as on GitHub.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    user_id: int
    login: str
    avatar_url: str | None = None
    state: ReviewerState

    @classmethod
    def latest(cls, reviews: Sequence[Review]) -> list[Self]:
        latest: dict[int, Self] = {}
        for review in sorted(reviews, key=lambda review: review.github_id):
            if review.author_github_id is None:
                continue
            previous = latest.get(review.author_github_id)
            if review.state == "commented" and previous and previous.state != "commented":
                continue
            latest[review.author_github_id] = cls(
                user_id=review.author_github_id,
                login=review.author_login,
                avatar_url=avatar_url(review.author_github_id),
                state=review.state,
            )
        return list(latest.values())

    @staticmethod
    def decision(reviewers: Sequence[PullRequestReviewer]) -> ReviewDecision:
        states = {reviewer.state for reviewer in reviewers}
        if "changes_requested" in states:
            return "changes_requested"
        return "approved" if "approved" in states else "none"


class OpenPullRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    repo: str
    number: int
    title: str
    state: PullRequestState = "open"
    created_at: str | None = None
    updated_at: str | None = None
    draft: bool | None = None
    additions: int | None = None
    deletions: int | None = None
    mergeable: bool | None = None
    merge_state: str = "unknown"
    head_sha: str | None = None
    head_ref: str | None = None
    status_available: bool = False
    ci: CheckState = "unknown"
    review_decision: ReviewDecision | None = None
    reviewers: list[PullRequestReviewer] | None = None
    review_required: bool = False
    unresolved_threads: int | None = None
    failing_checks: list[str] = Field(default_factory=list)
    pending_checks: list[str] = Field(default_factory=list)
    missing_checks: list[str] = Field(default_factory=list)


class OpenPullRequests(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    pull_requests: list[OpenPullRequest]
    next_page: int | None
    incomplete: bool
    updated_at: str


class SidebarPullRequest(TypedDict):
    """One PR's dot in the thread sidebar: check verdict plus open/merged/closed."""

    checks: Literal["failing", "passing", "pending", "unknown"]
    state: SidebarState | None


def unavailable_health(record: object) -> dict[str, Any]:
    """The health of a pull request nothing is known about."""
    full_name = record.get("repo_full_name") if isinstance(record, Mapping) else None
    number = record.get("number") if isinstance(record, Mapping) else None
    return {
        "repoFullName": full_name if isinstance(full_name, str) else None,
        "number": number if isinstance(number, int) and not isinstance(number, bool) else None,
        "url": None,
        "statusAvailable": False,
        "state": None,
        "isDraft": None,
        "mergeConflictState": None,
        "checksAvailable": False,
        "failingChecks": [],
        "pendingCheckCount": None,
        "inconclusiveCheckCount": None,
        "commentsAvailable": False,
        "unresolvedReviewThreadCount": None,
        "unresolvedReviewThreads": [],
    }


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


@dataclass(frozen=True, slots=True)
class PullRequestView:
    """One stored pull request with the CI, reviews and threads its views need."""

    pull: PullRequest
    checks: HeadChecks
    reviews: tuple[Review, ...]
    threads: tuple[ReviewThread, ...]

    @classmethod
    async def load(cls, pulls: Sequence[PullRequest]) -> list[Self]:
        if not pulls:
            return []
        ids = [pull.id for pull in pulls]
        async with postgres.session() as session:
            checks = await HeadChecks.for_heads(
                session, ((pull.repository_id, pull.head_sha) for pull in pulls)
            )
            reviews = await session.scalars(select(Review).where(Review.pull_request_id.in_(ids)))
            threads = await session.scalars(
                select(ReviewThread)
                .where(ReviewThread.pull_request_id.in_(ids))
                .order_by(ReviewThread.root_comment_github_id)
            )
            reviews_by_pull: dict[UUID, list[Review]] = {}
            for review in reviews:
                reviews_by_pull.setdefault(review.pull_request_id, []).append(review)
            threads_by_pull: dict[UUID, list[ReviewThread]] = {}
            for thread in threads:
                threads_by_pull.setdefault(thread.pull_request_id, []).append(thread)
        return [
            cls(
                pull,
                checks.get((pull.repository_id, pull.head_sha), HeadChecks((), ())),
                tuple(reviews_by_pull.get(pull.id, [])),
                tuple(threads_by_pull.get(pull.id, [])),
            )
            for pull in pulls
        ]

    @property
    def described(self) -> bool:
        """Whether any description of the PR from GitHub has reached the mirror."""
        return self.pull.github_updated_at is not None

    @property
    def state(self) -> PullRequestState:
        return "open" if self.pull.state == "draft" else self.pull.state

    @property
    def unresolved(self) -> list[ReviewThread]:
        return [thread for thread in self.threads if not thread.resolved]

    @property
    def merge_conflict_state(self) -> Literal["conflicting", "mergeable", "unknown"]:
        if self.pull.mergeable is False or self.pull.mergeable_state == "dirty":
            return "conflicting"
        if self.pull.mergeable is True and self.pull.mergeable_state == "clean":
            return "mergeable"
        return "unknown"

    async def summary(self) -> OpenPullRequest:
        pull = self.pull
        result = OpenPullRequest(
            repo=pull.repo_full_name,
            number=pull.number,
            title=pull.title,
            state=self.state,
            created_at=_iso(pull.github_created_at),
            updated_at=_iso(pull.github_updated_at),
            draft=pull.state == "draft" if self.described else None,
            additions=pull.additions,
            deletions=pull.deletions,
            mergeable=pull.mergeable,
            merge_state=pull.mergeable_state or "unknown",
            head_sha=pull.head_sha or None,
            head_ref=pull.head_ref or None,
            status_available=self.described,
        )
        if not self.described or not pull.head_sha:
            return result
        reviewers = PullRequestReviewer.latest(self.reviews)
        pending = self.checks.pending
        result.reviewers = reviewers
        result.review_decision = PullRequestReviewer.decision(reviewers)
        result.review_required = pull.review_decision == "review_required"
        result.unresolved_threads = len(self.unresolved) if pull.synced_at is not None else None
        result.failing_checks = [check.name or "Unnamed check" for check in self.checks.failing]
        result.pending_checks = pending
        result.ci = self.checks.state
        # A check gated on `needs:` has no run until its upstream jobs finish.
        if result.merge_state == "blocked" and not pending and pull.base_ref:
            required = await _required_checks(pull.owner, pull.repo, pull.base_ref)
            result.missing_checks = self.checks.unreported(required)
        return result

    def sidebar(self) -> SidebarPullRequest:
        if not self.described:
            return {"checks": "unknown", "state": None}
        state = self.checks.state
        return {
            # No checks configured reads as passing, so the row stays dot-free.
            "checks": "passing" if state == "none" else state,
            "state": self.pull.state,
        }

    async def health(self) -> dict[str, Any]:
        """The PR health a dashboard thread shows: state, checks and unresolved threads."""
        pull = self.pull
        result = unavailable_health({"repo_full_name": pull.repo_full_name, "number": pull.number})
        result["url"] = pull.url
        if not self.described:
            return result
        result.update(
            {
                "statusAvailable": True,
                "state": self.state,
                "isDraft": pull.state == "draft",
                "mergeConflictState": self.merge_conflict_state,
            }
        )
        if pull.head_sha:
            result.update(
                {
                    "checksAvailable": True,
                    "failingChecks": [
                        {
                            "name": check.name,
                            "conclusion": check.conclusion,
                            "url": check.url or None,
                        }
                        for check in self.checks.failing
                    ],
                    "pendingCheckCount": len(self.checks.pending),
                    "inconclusiveCheckCount": self.checks.inconclusive,
                }
            )
        if pull.synced_at is not None:
            threads = await self.unresolved_with_comments()
            result.update(
                {
                    "commentsAvailable": True,
                    "unresolvedReviewThreadCount": len(threads),
                    "unresolvedReviewThreads": threads,
                }
            )
        return result

    async def unresolved_with_comments(self) -> list[dict[str, Any]]:
        """Each unresolved thread's opening comment, with the replies under it."""
        roots = {thread.root_comment_github_id: thread for thread in self.unresolved}
        if not roots:
            return []
        async with postgres.session() as session:
            comments = await session.scalars(
                select(ReviewComment)
                .where(
                    ReviewComment.pull_request_id == self.pull.id,
                    or_(
                        ReviewComment.github_id.in_(roots),
                        ReviewComment.in_reply_to_github_id.in_(roots),
                    ),
                )
                .order_by(ReviewComment.github_id)
            )
            by_thread: dict[int, list[ReviewComment]] = {}
            for comment in comments:
                by_thread.setdefault(comment.thread_root, []).append(comment)
        threads: list[dict[str, Any]] = []
        for root_id, thread in roots.items():
            comments = by_thread.get(root_id, [])
            root = next((c for c in comments if c.github_id == root_id), None)
            if root is None:
                continue
            threads.append(
                {
                    "thread_id": thread.node_id or None,
                    "author": root.author_login or None,
                    "body": root.body,
                    "path": root.path,
                    "line": root.line if root.line is not None else root.original_line,
                    "url": root.html_url or None,
                    "replies": [
                        {
                            "author": reply.author_login or None,
                            "body": reply.body,
                            "url": reply.html_url or None,
                        }
                        for reply in comments
                        if reply.github_id != root_id
                    ],
                }
            )
        return threads


async def _required_checks(owner: str, repo: str, branch: str) -> list[tuple[str, int | None]]:
    """``(name, app_id)`` of the checks ``branch`` requires, as the App reads them."""

    async def load() -> list[tuple[str, int | None]]:
        try:
            async with GitHubClient.as_app(owner, repo) as github:
                required = await RequiredCheck.for_branch(github.repo(owner, repo), branch)
        except GitHubAppUnavailable:
            logger.info(
                "No GitHub App installation to read required checks",
                extra={"repository": f"{owner}/{repo}"},
            )
            return []
        return [(check.name, check.app_id) for check in required or ()]

    key = f"required-checks:{owner}/{repo}:{branch}".lower()
    return await ttl_cache.cached(key, _REQUIRED_CHECKS_TTL, load)


async def _viewer_teams(login: str, repositories: Collection[str]) -> set[tuple[str, str]]:
    """``(org, team)`` pairs, lowercased, that open PRs in ``repositories`` ask a review of and ``login`` is in."""
    slugs = func.jsonb_array_elements_text(
        func.jsonb_path_query_array(PullRequest.requested_teams, cast("$[*].slug", JSONPATH))
    )
    async with postgres.session() as session:
        rows = await session.execute(
            select(func.lower(PullRequest.owner), slugs)
            .join(PullRequest.repository)
            .where(
                Repository.key.in_(repositories),
                PullRequest.state.in_(_OPEN_STATES),
                PullRequest.requested_teams != [],
            )
            .distinct()
        )
        requested = {(org, slug) for org, slug in rows if slug}

    async def member_of(org: str, slug: str) -> bool:
        async def load() -> list[str]:
            return [member.lower() for member in await team_members(org, slug) or []]

        members = await ttl_cache.cached(f"team-members:{org}/{slug}", _TEAM_MEMBERS_TTL, load)
        return login.lower() in members

    pairs = sorted(requested)
    memberships = await asyncio.gather(*(member_of(org, slug) for org, slug in pairs))
    return {pair for pair, member in zip(pairs, memberships, strict=True) if member}


async def list_open(
    login: str,
    *,
    repositories: Collection[str],
    scope: Literal["mine", "review-requested"],
    sort: Literal["created", "updated"],
    direction: Literal["asc", "desc"],
    page: int,
) -> OpenPullRequests:
    """The open PRs ``login`` authored, or is asked to review, in ``repositories``."""
    keys = sorted({repository.lower() for repository in repositories})
    user = await User.for_login("github", login)
    viewer_id = user.github_id if user is not None else None
    if scope == "mine":
        whose: ColumnElement[bool] = (
            PullRequest.author_github_id == viewer_id
            if viewer_id is not None
            else func.lower(PullRequest.author) == login.lower()
        )
    else:
        teams = await _viewer_teams(login, keys)
        whose = or_(
            PullRequest.requested_reviewers.contains([{"id": viewer_id}])
            if viewer_id is not None
            else PullRequest.requested_reviewers.contains([{"login": login}]),
            *(
                and_(
                    func.lower(PullRequest.owner) == org,
                    PullRequest.requested_teams.contains([{"slug": slug}]),
                )
                for org, slug in teams
            ),
        )
    column = PullRequest.github_created_at if sort == "created" else PullRequest.github_updated_at
    order = column.desc().nulls_last() if direction == "desc" else column.asc().nulls_last()
    async with postgres.session() as session:
        rows = list(
            await session.scalars(
                select(PullRequest)
                .join(PullRequest.repository)
                .where(Repository.key.in_(keys), PullRequest.state.in_(_OPEN_STATES), whose)
                .order_by(order, PullRequest.id)
                .limit(PAGE_SIZE + 1)
                .offset((page - 1) * PAGE_SIZE)
            )
        )
    views = await PullRequestView.load(rows[:PAGE_SIZE])
    return OpenPullRequests(
        pull_requests=list(await asyncio.gather(*(view.summary() for view in views))),
        next_page=page + 1 if len(rows) > PAGE_SIZE else None,
        incomplete=False,
        updated_at=datetime.now(UTC).isoformat(),
    )
