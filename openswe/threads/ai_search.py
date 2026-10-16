"""Natural-language thread search: Jev judges which recent threads a query describes."""

from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from openswe.prompts import prompt
from openswe.threads.listing import DashboardThreadScope, list_dashboard_threads_page
from openswe.utils.jev_search import JEV_SEARCH_PAGE_SIZE, JevBatch, JevSearch

AI_SEARCH_CANDIDATES = 60
AI_SEARCH_SCAN_CAP = 200


def stream_thread_matches(
    login: str, *, email: str | None, query: str, scope: DashboardThreadScope
) -> AsyncIterator[JevBatch[dict[str, object]]]:
    """Judge the person's threads newest first, a page at a time, up to the scan cap."""
    return _thread_search(query).stream(
        _recent_thread_pages(login, email=email, scope=scope), _ThreadCard.model_validate
    )


async def rank_threads(
    query: str, threads: Sequence[Mapping[str, object]]
) -> list[dict[str, object]]:
    """Keep the threads Jev thinks `query` is looking for, most likely first."""
    matches = await _thread_search(query).rank(threads, _ThreadCard.model_validate)
    return [with_relevance(match.item, match.relevance) for match in matches]


def with_relevance(thread: Mapping[str, object], relevance: float) -> dict[str, object]:
    return {**thread, "relevance": round(relevance, 3)}


def _thread_search(query: str) -> JevSearch:
    return JevSearch(query, "thread", instructions=prompt("threads/search-match"))


async def _recent_thread_pages(
    login: str, *, email: str | None, scope: DashboardThreadScope
) -> AsyncIterator[list[dict[str, object]]]:
    for offset in range(0, AI_SEARCH_SCAN_CAP, JEV_SEARCH_PAGE_SIZE):
        page = await list_dashboard_threads_page(
            login, email=email, limit=JEV_SEARCH_PAGE_SIZE, offset=offset, scope=scope
        )
        items: list[dict[str, object]] = page["items"]
        if items:
            yield items
        if not page["hasMore"]:
            return


class _LinkedPullRequest(BaseModel):
    title: str | None = None
    state: str | None = None


class _ThreadCard(BaseModel):
    """What Jev sees of a thread summary when judging whether a search means it."""

    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    repo: str | None = Field(None, validation_alias="repoFullName")
    branch: str | None = None
    source: str | None = None
    automation: str | None = Field(None, validation_alias="automationName")
    status: str | None = None
    resolved: bool = False
    pull_requests: list[_LinkedPullRequest] = Field([], validation_alias="pullRequests")
    created: datetime | None = Field(None, validation_alias="createdAt")
    updated: datetime | None = Field(None, validation_alias="updatedAt")
