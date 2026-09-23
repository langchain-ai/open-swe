from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from agent.review.reviews import ReviewScoutTrigger
from agent.tools.request_pr_review import request_pr_review


@pytest.mark.asyncio
async def test_request_pr_review_parses_pull_request_url() -> None:
    trigger = AsyncMock(return_value=ReviewScoutTrigger(started=True, run_id="run-1"))
    with (
        patch(
            "agent.tools.request_pr_review.RunConfig.from_runtime",
            return_value=SimpleNamespace(github_login="octocat", user_email=None),
        ),
        patch(
            "agent.tools.request_pr_review.require_repo_access_for_user",
            new_callable=AsyncMock,
        ) as access,
        patch("agent.tools.request_pr_review.trigger_review_scout", trigger),
    ):
        result = await request_pr_review("https://github.com/acme/widgets/pull/42")

    assert result == {
        "success": True,
        "started": True,
        "run_id": "run-1",
        "owner": "acme",
        "repo": "widgets",
        "pr_number": 42,
    }
    access.assert_awaited_once_with("octocat", "acme/widgets")
    trigger.assert_awaited_once_with("acme", "widgets", 42)


@pytest.mark.asyncio
async def test_request_pr_review_accepts_explicit_repository_coordinates() -> None:
    trigger = AsyncMock(return_value=ReviewScoutTrigger(started=False))
    with (
        patch(
            "agent.tools.request_pr_review.RunConfig.from_runtime",
            return_value=SimpleNamespace(github_login="octocat", user_email=None),
        ),
        patch(
            "agent.tools.request_pr_review.require_repo_access_for_user",
            new_callable=AsyncMock,
        ),
        patch("agent.tools.request_pr_review.trigger_review_scout", trigger),
    ):
        result = await request_pr_review(owner="acme", repo="widgets", pr_number=42)

    assert result["success"] is True
    assert result["started"] is False
    assert result["run_id"] is None
    trigger.assert_awaited_once_with("acme", "widgets", 42)


@pytest.mark.asyncio
async def test_request_pr_review_returns_entitlement_error() -> None:
    trigger = AsyncMock()
    with (
        patch(
            "agent.tools.request_pr_review.RunConfig.from_runtime",
            return_value=SimpleNamespace(github_login="octocat", user_email=None),
        ),
        patch(
            "agent.tools.request_pr_review.require_repo_access_for_user",
            AsyncMock(side_effect=HTTPException(403, "no repository access")),
        ),
        patch("agent.tools.request_pr_review.trigger_review_scout", trigger),
    ):
        result = await request_pr_review(owner="acme", repo="widgets", pr_number=42)

    assert result == {"success": False, "error": "no repository access"}
    trigger.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_pr_review_surfaces_trigger_failure() -> None:
    with (
        patch(
            "agent.tools.request_pr_review.RunConfig.from_runtime",
            return_value=SimpleNamespace(github_login="octocat", user_email=None),
        ),
        patch(
            "agent.tools.request_pr_review.require_repo_access_for_user",
            new_callable=AsyncMock,
        ),
        patch(
            "agent.tools.request_pr_review.trigger_review_scout",
            AsyncMock(side_effect=RuntimeError("review scout unavailable")),
        ),
    ):
        result = await request_pr_review(owner="acme", repo="widgets", pr_number=42)

    assert result == {"success": False, "error": "review scout unavailable"}
