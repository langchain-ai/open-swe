"""Natural-language thread search: Jev judges which recent threads a query describes."""

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from langchain_typesafe import ClassifierResponse, Noul
from pydantic import BaseModel, ConfigDict, Field

from openswe.prompts import prompt
from openswe.threads.listing import DashboardThreadScope, list_dashboard_threads_page
from openswe.utils.jev import classify_each_with_jev

AI_SEARCH_CANDIDATES = 60
_AI_SEARCH_TIMEOUT_SECONDS = 10.0
_MATCH_THRESHOLD = 0.5
_JEV_CONCURRENCY = 16

logger = logging.getLogger(__name__)


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
    """Keep the threads Jev thinks `query` is looking for, most likely first.

    Each thread is its own Jev request, since Jev answers every question against one state.
    """
    if not threads:
        return []
    today = datetime.now(UTC).date().isoformat()
    question = Noul(instructions=prompt("threads/search-match"))
    responses = await classify_each_with_jev(
        [
            {
                "state": {
                    "query": query,
                    "today": today,
                    "thread": _ThreadCard.model_validate(thread).model_dump(
                        mode="json", exclude_none=True
                    ),
                },
                "questions": {"match": question},
            }
            for thread in threads
        ],
        timeout=_AI_SEARCH_TIMEOUT_SECONDS,
        max_concurrency=_JEV_CONCURRENCY,
    )
    failures = [response for response in responses if isinstance(response, Exception)]
    if failures and len(failures) == len(responses):
        raise failures[0]
    if failures:
        logger.warning(
            "Jev could not judge some threads",
            extra={"failed": len(failures), "candidates": len(responses)},
            exc_info=failures[0],
        )
    matches = sorted(
        (
            (answer.noul, index)
            for index, response in enumerate(responses)
            if isinstance(response, ClassifierResponse)
            and (answer := response.nouls.get("match")) is not None
            and answer.noul >= _MATCH_THRESHOLD
        ),
        key=lambda match: match[0],
        reverse=True,
    )
    return [{**threads[index], "relevance": round(score, 3)} for score, index in matches]


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
