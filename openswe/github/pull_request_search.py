"""Natural-language pull request search: Jev judges which of a person's PRs a query describes."""

from collections.abc import AsyncIterator
from datetime import datetime

from pydantic import BaseModel

from openswe.github.comments import PrState
from openswe.github.pull_requests import PullRequest
from openswe.github.repos import accessible_repo_full_names
from openswe.prompts import prompt
from openswe.utils.jev_search import JEV_SEARCH_PAGE_SIZE, JevBatch, JevSearch

AI_SEARCH_SCAN_CAP = 200
_BODY_CHARS = 2000


class _PullRequestCard(BaseModel):
    """What Jev sees of a pull request when judging whether a search means it."""

    repo: str
    number: int
    title: str
    state: PrState
    head_branch: str | None
    base_branch: str | None
    description: str | None
    updated: datetime | None

    @classmethod
    def of(cls, pr: PullRequest) -> _PullRequestCard:
        return cls(
            repo=pr.repo_full_name,
            number=pr.number,
            title=pr.title,
            state=pr.state,
            head_branch=pr.head_ref or None,
            base_branch=pr.base_ref or None,
            description=pr.body[:_BODY_CHARS] or None,
            updated=pr.updated_at,
        )


def stream_pull_request_matches(login: str, query: str) -> AsyncIterator[JevBatch[PullRequest]]:
    """Judge the person's PRs most recently updated first, a page at a time, up to the scan cap."""
    search = JevSearch(query, "pull_request", instructions=prompt("pull-requests/search-match"))
    return search.stream(_authored_pages(login), _PullRequestCard.of)


async def _authored_pages(login: str) -> AsyncIterator[list[PullRequest]]:
    repositories = sorted(await accessible_repo_full_names(login))
    for offset in range(0, AI_SEARCH_SCAN_CAP, JEV_SEARCH_PAGE_SIZE):
        page = await PullRequest.recent_by_author(
            login, repositories=repositories, limit=JEV_SEARCH_PAGE_SIZE, offset=offset
        )
        if page:
            yield page
        if len(page) < JEV_SEARCH_PAGE_SIZE:
            return
