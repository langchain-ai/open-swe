"""The viewer's prioritized, live queue of pull requests to work through."""

import asyncio
from datetime import UTC, datetime
from typing import Literal

from openswe.github.http import GitHubClient
from openswe.github.pull_request_status import (
    OpenPullRequest,
    OpenPullRequests,
    list_open_pull_requests,
)
from openswe.github.pull_requests import PullRequest
from openswe.human_review.requests import HumanReviewRequest
from openswe.users import User


class ReviewQueue:
    def __init__(self, github: GitHubClient, login: str, repositories: set[str]):
        self.github = github
        self.login = login
        self.repositories = repositories
        self.incomplete = False
        self.semaphore = asyncio.Semaphore(4)

    async def search(self, scope: Literal["drafts", "review-requested"]) -> list[OpenPullRequest]:
        rows: list[OpenPullRequest] = []
        page = 1
        while True:
            result = await list_open_pull_requests(
                self.github,
                self.login,
                ",".join(sorted(self.repositories)),
                lightweight=True,
                page=page,
                scope=scope,
            )
            rows.extend(result.pull_requests)
            self.incomplete |= result.incomplete
            if result.next_page is None:
                return rows
            page = result.next_page

    async def load(self, sort: str, direction: str, page: int) -> OpenPullRequests:
        if not self.repositories:
            return OpenPullRequests(
                pull_requests=[],
                next_page=None,
                incomplete=False,
                updated_at=datetime.now(UTC).isoformat(),
            )
        user = await User.for_login("github", self.login)
        assignments = await HumanReviewRequest.assigned_to(user.id) if user else []
        requested, drafts = await asyncio.gather(
            self.search("review-requested"), self.search("drafts")
        )
        speculative = await PullRequest.review_candidates(self.repositories, self.login)
        assigned = {
            (request.pull_request.repo_full_name.lower(), request.pull_request.number)
            for request in assignments
        }
        candidates = {
            (row.repo.lower(), row.number): row
            for row in [
                *requested,
                *drafts,
                *[
                    OpenPullRequest(repo=pr.repo_full_name, number=pr.number, title=pr.title)
                    for pr in [*(request.pull_request for request in assignments), *speculative]
                ],
            ]
            if row.repo.lower() in self.repositories
        }
        required = assigned | {(row.repo.lower(), row.number) for row in requested}
        loaded = await asyncio.gather(
            *(self.pending(key, row, required) for key, row in candidates.items())
        )
        rows = [item for item in loaded if item is not None]
        rows.sort(
            key=lambda item: (
                (item[1].created_at if sort == "created" else item[1].updated_at) or "",
                item[1].repo,
                item[1].number,
            ),
            reverse=direction == "desc",
        )
        rows.sort(key=lambda item: item[0])
        start = (page - 1) * 100
        return OpenPullRequests(
            pull_requests=[row for _, row in rows[start : start + 100]],
            next_page=page + 1 if start + 100 < len(rows) else None,
            incomplete=self.incomplete,
            updated_at=datetime.now(UTC).isoformat(),
        )

    async def pending(
        self, key: tuple[str, int], row: OpenPullRequest, required: set[tuple[str, int]]
    ) -> tuple[int, OpenPullRequest] | None:
        owner, repo = row.repo.split("/", 1)
        async with self.semaphore:
            live = (
                await self.github.repo(owner, repo).pull_request(row.number).load(wanted={"open"})
            )
        if live is None:
            return None
        if live.author is None:
            return None
        if live.author.lower() == self.login.lower():
            return (1, live) if live.draft else None
        if live.draft is not False or live.reviewers is None:
            return None
        if any(
            reviewer.login.lower() == self.login.lower()
            and reviewer.state in {"approved", "changes_requested"}
            for reviewer in live.reviewers
        ):
            return None
        return (0 if key in required else 2, live)
