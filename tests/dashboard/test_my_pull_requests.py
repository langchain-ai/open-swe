from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from openswe.github import pull_request_dashboard_routes as pr_routes
from openswe.github.repos import ReadableRepositories
from openswe.review import routes as review_routes


@pytest.mark.parametrize("repo", ["acme/app is:closed", "acme/../secrets", "acme/.."])
async def test_repository_filter_must_name_a_repository(repo: str) -> None:
    with pytest.raises(HTTPException) as error:
        await pr_routes.api_list_pull_requests(
            repo=repo,
            sort="updated",
            direction="desc",
            page=1,
            scope="mine",
            session={"sub": "octocat"},
        )
    assert error.value.status_code == 422


async def test_review_indicators_require_repo_access_before_reading(monkeypatch):
    lookup = AsyncMock(return_value={})
    monkeypatch.setattr(review_routes, "get_review_summaries", lookup)
    monkeypatch.setattr(
        ReadableRepositories,
        "of",
        AsyncMock(return_value=ReadableRepositories(frozenset({"acme/allowed"}))),
    )
    payload = review_routes.ReviewSummariesRequest(
        pull_requests=[{"repo": "acme/private", "number": 1}]
    )
    assert await review_routes.api_get_review_summaries(payload, session={"sub": "octocat"}) == {}
    lookup.assert_not_awaited()
    payload = review_routes.ReviewSummariesRequest(
        pull_requests=[
            {"repo": "acme/allowed", "number": 1},
            {"repo": "acme/private", "number": 2},
        ]
    )
    await review_routes.api_get_review_summaries(payload, session={"sub": "octocat"})
    lookup.assert_awaited_once_with([("acme", "allowed", 1)])
