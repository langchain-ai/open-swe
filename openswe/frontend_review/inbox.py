"""Design QA's inbox: open pull requests with captures, in the repositories a person shared."""

import asyncio
from collections.abc import Sequence
from typing import Literal

from openswe.github.http import GitHubClient
from openswe.github.pull_request_status import OpenPullRequest, list_open_pull_requests

from .schemas import FrontendReviewInbox, FrontendReviewInboxItem
from .service import latest_reviews, review_view


class ReviewInbox:
    """What one person is asked to review, or wrote, among repositories they shared with an app."""

    def __init__(self, login: str, repositories: Sequence[str]) -> None:
        self.login = login
        self.repositories = sorted(repositories)

    async def read(self, scope: Literal["review-requested", "mine"]) -> FrontendReviewInbox:
        if not self.repositories:
            return FrontendReviewInbox(items=[], incomplete=False)
        async with GitHubClient.as_user(self.login) as github:
            # GitHub's review-requested qualifier includes requests to the person's teams.
            listed = await list_open_pull_requests(
                github, self.login, ",".join(self.repositories), scope=scope, lightweight=True
            )
            runs = {
                (run.owner.lower(), run.repo.lower(), run.pr_number): run
                for run in await latest_reviews([self._identity(pr) for pr in listed.pull_requests])
            }
            semaphore = asyncio.Semaphore(4)

            async def item(pr: OpenPullRequest) -> FrontendReviewInboxItem | None:
                owner, repo, number = self._identity(pr)
                run = runs.get((owner.lower(), repo.lower(), number))
                if run is None:
                    return None
                async with semaphore:
                    pull = await github.repo(owner, repo).pull_request(number).pull()
                if pull.get("state") != "open":
                    return None
                head = pull.get("head")
                if isinstance(head, dict) and isinstance(head.get("sha"), str):
                    pr.head_sha = head["sha"]
                reviewers = pull.get("requested_reviewers")
                teams = pull.get("requested_teams")
                return FrontendReviewInboxItem(
                    pr=pr,
                    run=review_view(run),
                    requested_reviewers=[
                        reviewer["login"]
                        for reviewer in reviewers
                        if isinstance(reviewer, dict) and isinstance(reviewer.get("login"), str)
                    ]
                    if isinstance(reviewers, list)
                    else [],
                    requested_teams=[
                        f"{owner}/{team['slug']}"
                        for team in teams
                        if isinstance(team, dict) and isinstance(team.get("slug"), str)
                    ]
                    if isinstance(teams, list)
                    else [],
                )

            items = await asyncio.gather(*(item(pr) for pr in listed.pull_requests))
        return FrontendReviewInbox(
            items=[row for row in items if row is not None],
            # Past GitHub's first page of results, the oldest pull requests are left out.
            incomplete=listed.incomplete or listed.next_page is not None,
        )

    @staticmethod
    def _identity(pr: OpenPullRequest) -> tuple[str, str, int]:
        owner, repo = pr.repo.split("/", 1)
        return owner, repo, pr.number
