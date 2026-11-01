"""One person's To Review list.

First the pull requests Open SWE assigned them, then those its unclaimed deadline
may pick them for, then those linked in Slack threads that mention them, either by
their GitHub URL or by this deployment's review page. A pull request they already
approved or requested changes on drops off.
"""

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, Self

import httpx2
from fastapi import HTTPException

from openswe.dashboard.profiles import PROFILES
from openswe.expedited_review.readiness import latest_review_states
from openswe.github.http import GitHubClient, GitHubError
from openswe.github.pull_request_status import OpenPullRequest
from openswe.github.pull_request_status import ReviewAssignment as Assignment
from openswe.human_review.requests import HumanReviewRequest
from openswe.human_review.tentative import TentativeReviewers
from openswe.slack.client import GitHubPrRef, parse_github_pr_url
from openswe.slack.thread_mentions import SlackThreadMention
from openswe.users import User

logger = logging.getLogger(__name__)

_ORDER: tuple[Assignment, ...] = ("assigned", "tentative", "mentioned")
MENTION_WINDOW = timedelta(days=30)
MENTION_LIMIT = 50


@dataclass(frozen=True, slots=True)
class QueuedPullRequest:
    """A pull request on the list before GitHub confirms it still needs this person."""

    ref: GitHubPrRef
    assignment: Assignment
    request: HumanReviewRequest | None = None

    @property
    def key(self) -> str:
        return self.ref.url.lower()

    @property
    def repo_full_name(self) -> str:
        return f"{self.ref.owner}/{self.ref.repo}"

    @classmethod
    def of_request(cls, request: HumanReviewRequest, assignment: Assignment) -> Self:
        pr = request.pull_request
        ref = GitHubPrRef(owner=pr.owner, repo=pr.repo, number=pr.number, url=pr.url)
        return cls(ref, assignment, request)


class ReviewQueue:
    """The To Review list of ``login``, read from GitHub as them."""

    def __init__(
        self, github: GitHubClient, login: str, user: User, repositories: set[str]
    ) -> None:
        self.github = github
        self.login = login
        self.user = user
        self.repositories = repositories
        self._gate = asyncio.Semaphore(4)

    def _wanted(self, repo_full_name: str) -> bool:
        return not self.repositories or repo_full_name.lower() in self.repositories

    async def assigned(self) -> list[QueuedPullRequest]:
        requests = await HumanReviewRequest.assigned_to(self.user.id)
        return [QueuedPullRequest.of_request(request, "assigned") for request in requests]

    async def unclaimed(self) -> list[HumanReviewRequest]:
        """Unclaimed requests the deadline could pick this person for, by who they are alone."""
        if not await PROFILES.allows_auto_human_review(self.login):
            return []
        return [
            request
            for request in await HumanReviewRequest.unclaimed()
            if not request.is_author(self.user.id, self.login)
            and request.participant(self.user.id) is None
        ]

    async def tentative(self, requests: list[HumanReviewRequest]) -> list[QueuedPullRequest]:
        async def picks_me(request: HumanReviewRequest) -> bool:
            async with self._gate:
                try:
                    return self.login.lower() in await TentativeReviewers(request).logins()
                except httpx2.HTTPError, GitHubError:
                    logger.warning(
                        "Could not work out a review request's tentative reviewers",
                        extra={"request_id": str(request.id)},
                        exc_info=True,
                    )
                    return False

        matches = await asyncio.gather(*(picks_me(request) for request in requests))
        return [
            QueuedPullRequest.of_request(request, "tentative")
            for request, match in zip(requests, matches, strict=True)
            if match
        ]

    async def mentioned(self) -> list[QueuedPullRequest]:
        urls = await SlackThreadMention.linked_pull_requests(
            self.user.id, within=MENTION_WINDOW, limit=MENTION_LIMIT
        )
        return [
            QueuedPullRequest(ref, "mentioned")
            for url in urls
            if (ref := parse_github_pr_url(url)) is not None
        ]

    async def accessible(self, repo_full_names: set[str]) -> set[str]:
        """The repositories this person can still read, lowercased."""
        allowed: set[str] = set()
        for full_name in repo_full_names:
            owner, name = full_name.split("/", 1)
            try:
                await self.github.repo(owner, name).info()
            except GitHubError as exc:
                if exc.response.status_code not in {403, 404}:
                    raise
                logger.info(
                    "To Review repository no longer accessible",
                    extra={"repository": full_name},
                )
            else:
                allowed.add(full_name.lower())
        return allowed

    async def _live(self, queued: QueuedPullRequest) -> Mapping[str, object] | None:
        """The open PR's GitHub payload, or ``None`` once it is closed or unreadable."""
        pull = self.github.repo(queued.ref.owner, queued.ref.repo).pull_request(queued.ref.number)
        try:
            payload = await pull.pull()
        except GitHubError, httpx2.HTTPError, ValueError:
            logger.warning(
                "Could not read a mentioned pull request",
                extra={"pr_url": queued.ref.url},
                exc_info=True,
            )
            return None
        return payload if payload.get("state") == "open" else None

    async def _row(self, queued: QueuedPullRequest) -> tuple[OpenPullRequest, str] | None:
        """The row and the PR's author, or ``None`` for a closed PR this person wrote."""
        ref = queued.ref
        if (request := queued.request) is not None:
            pr = request.pull_request
            row = OpenPullRequest(
                repo=pr.repo_full_name,
                number=ref.number,
                title=pr.title,
                created_at=request.created_at.isoformat() if request.created_at else None,
                updated_at=request.updated_at.isoformat() if request.updated_at else None,
                details_loading=True,
                assignment=queued.assignment,
            )
            return row, pr.author
        payload = await self._live(queued)
        if payload is None:
            return None
        user = payload.get("user")
        author = user.get("login") if isinstance(user, Mapping) else None
        if not isinstance(author, str) or author.lower() == self.login.lower():
            return None
        created, updated, title = (payload.get(k) for k in ("created_at", "updated_at", "title"))
        row = OpenPullRequest(
            repo=queued.repo_full_name,
            number=ref.number,
            title=title if isinstance(title, str) else "",
            created_at=created if isinstance(created, str) else None,
            updated_at=updated if isinstance(updated, str) else None,
            details_loading=True,
            assignment=queued.assignment,
        )
        return row, author

    async def pending(self, queued: QueuedPullRequest) -> OpenPullRequest | None:
        """The row, unless this person wrote the PR or already approved or blocked it."""
        ref = queued.ref
        pull = self.github.repo(ref.owner, ref.repo).pull_request(ref.number)
        async with self._gate:
            found = await self._row(queued)
            if found is None:
                return None
            row, author = found
            states = await latest_review_states(pull, author)
        if states is None:
            if queued.assignment == "assigned":
                raise HTTPException(502, "Could not load assigned reviews from GitHub")
            logger.warning("Could not read a queued PR's reviews", extra={"pr_url": ref.url})
            return None
        if any(
            who.lower() == self.login.lower() and state in {"APPROVED", "CHANGES_REQUESTED"}
            for who, state in states.items()
        ):
            return None
        return row

    async def rows(
        self, sort: Literal["created", "updated"], direction: Literal["asc", "desc"]
    ) -> list[OpenPullRequest]:
        """Every row, assigned first, then tentative, then mentioned, each group sorted."""
        assigned, unclaimed, mentioned = await asyncio.gather(
            self.assigned(), self.unclaimed(), self.mentioned()
        )
        unclaimed = [r for r in unclaimed if self._wanted(r.pull_request.repo_full_name)]
        queued = [q for q in [*assigned, *mentioned] if self._wanted(q.repo_full_name)]
        allowed = await self.accessible(
            {q.repo_full_name for q in queued} | {r.pull_request.repo_full_name for r in unclaimed}
        )
        tentative = await self.tentative(
            [r for r in unclaimed if r.pull_request.repo_full_name.lower() in allowed]
        )
        seen: set[str] = set()
        distinct: list[QueuedPullRequest] = []
        for item in sorted([*queued, *tentative], key=lambda item: _ORDER.index(item.assignment)):
            if item.repo_full_name.lower() in allowed and item.key not in seen:
                seen.add(item.key)
                distinct.append(item)
        loaded = await asyncio.gather(*(self.pending(item) for item in distinct))
        rows = [row for row in loaded if row is not None]
        rows.sort(
            key=lambda row: (
                (row.created_at if sort == "created" else row.updated_at) or "",
                row.repo,
                row.number,
            ),
            reverse=direction == "desc",
        )
        rows.sort(key=lambda row: _ORDER.index(row.assignment or "mentioned"))
        return rows
