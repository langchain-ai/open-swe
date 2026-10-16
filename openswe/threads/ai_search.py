"""Natural-language thread search: Jev judges which recent threads a query describes."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from langchain_typesafe import Noul
from pydantic import BaseModel, ConfigDict, Field

from openswe.prompts import prompt
from openswe.threads.listing import DashboardThreadScope, list_dashboard_threads_page
from openswe.utils.jev import classify_with_jev

AI_SEARCH_CANDIDATES = 60
_AI_SEARCH_TIMEOUT_SECONDS = 10.0
_MATCH_THRESHOLD = 0.5


async def search_dashboard_threads(
    login: str,
    *,
    email: str | None,
    query: str,
    scope: DashboardThreadScope,
    limit: int,
) -> list[dict[str, object]]:
    page = await list_dashboard_threads_page(
        login, email=email, limit=AI_SEARCH_CANDIDATES, scope=scope
    )
    return (await rank_threads(query, page["items"]))[:limit]


async def rank_threads(
    query: str, threads: Sequence[Mapping[str, object]]
) -> list[dict[str, object]]:
    """Keep the threads Jev thinks `query` is looking for, most likely first."""
    by_id = {str(thread["id"]): thread for thread in threads}
    if not by_id:
        return []
    response = await classify_with_jev(
        {
            "query": query,
            "today": datetime.now(UTC).date().isoformat(),
            "threads": {
                thread_id: _ThreadCard.model_validate(thread).model_dump(
                    mode="json", exclude_none=True
                )
                for thread_id, thread in by_id.items()
            },
        },
        {
            thread_id: Noul(instructions=prompt("threads/search-match", thread_id=thread_id))
            for thread_id in by_id
        },
        timeout=_AI_SEARCH_TIMEOUT_SECONDS,
    )
    matches = sorted(
        (
            (answer.noul, thread_id)
            for thread_id, answer in response.nouls.items()
            if thread_id in by_id and answer.noul >= _MATCH_THRESHOLD
        ),
        reverse=True,
    )
    return [{**by_id[thread_id], "relevance": round(score, 3)} for score, thread_id in matches]


class _PullRequestCard(BaseModel):
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
    pull_requests: list[_PullRequestCard] = Field([], validation_alias="pullRequests")
    created: datetime | None = Field(None, validation_alias="createdAt")
    updated: datetime | None = Field(None, validation_alias="updatedAt")
