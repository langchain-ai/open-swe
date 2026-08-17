from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.review.publish import fetch_approval_preflight, post_pull_request_review


def _response(payload: object) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    response.status_code = 200
    return response


def _client_context() -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=MagicMock())
    context.__aexit__ = AsyncMock(return_value=None)
    return context


@pytest.mark.asyncio
async def test_approval_preflight_detects_change_request_and_duplicate() -> None:
    pr = {
        "head": {"sha": "head"},
        "user": {"login": "alice"},
        "state": "open",
        "draft": False,
        "merged": False,
    }
    reviews = [
        {
            "id": 1,
            "state": "CHANGES_REQUESTED",
            "commit_id": "head",
            "body": "needs work",
            "user": {"login": "bob", "type": "User"},
        },
        {
            "id": 2,
            "state": "APPROVED",
            "commit_id": "head",
            "body": "<!-- open-swe-reviewer pr=7 -->",
            "user": {"login": "open-swe[bot]", "type": "Bot"},
        },
    ]
    request = AsyncMock(side_effect=[_response(pr), _response(reviews)])

    with (
        patch("agent.review.publish.get_github_app_slug", AsyncMock(return_value="open-swe")),
        patch("agent.review.publish.github_client", return_value=_client_context()),
        patch("agent.review.publish.github_request", request),
    ):
        result = await fetch_approval_preflight(
            owner="o", repo="r", pr_number=7, assessed_sha="head", token="token"
        )

    assert result is not None
    assert result["active_human_change_requests"] == ["bob"]
    assert result["duplicate_approval_review_id"] == 2


@pytest.mark.asyncio
async def test_post_pull_request_review_sends_approve_event() -> None:
    request = AsyncMock(return_value=_response({"id": 3}))

    with (
        patch("agent.review.publish.github_client", return_value=_client_context()),
        patch("agent.review.publish.github_request", request),
    ):
        result = await post_pull_request_review(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="head",
            body="body",
            inline_comments=[],
            token="token",
            event="APPROVE",
        )

    assert result == {"id": 3}
    assert request.await_args is not None
    assert request.await_args.kwargs["json"]["event"] == "APPROVE"
