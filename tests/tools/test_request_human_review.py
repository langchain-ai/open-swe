from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.tools.request_human_review import assign_human_reviewer


@pytest.mark.asyncio
async def test_assign_human_reviewer_refuses_non_owner() -> None:
    request = SimpleNamespace(kind="standard", thread_id="thread")
    with (
        patch(
            "agent.tools.request_human_review.parse_github_pr_url",
            return_value=SimpleNamespace(owner="o", repo="r", number=1),
        ),
        patch(
            "agent.tools.request_human_review.HumanReviewRequest.active_for",
            AsyncMock(return_value=request),
        ),
        patch("agent.tools.request_human_review.get_config", return_value={}),
        patch(
            "agent.tools.request_human_review.RunConfig.from_config",
            return_value=SimpleNamespace(thread_id="thread"),
        ),
        patch("agent.tools.request_human_review._repository_refusal", AsyncMock(return_value=None)),
        patch(
            "agent.tools.request_human_review.resolve_github_token",
            AsyncMock(return_value=("token", None)),
        ),
        patch(
            "agent.tools.request_human_review.login_is_codeowner",
            AsyncMock(return_value=(False, {"@owner"}, False)),
        ),
        patch("agent.tools.request_human_review.assign", AsyncMock()) as assign,
    ):
        result = await assign_human_reviewer("https://github.com/o/r/pull/1", "someone")
    assert result == {
        "success": False,
        "error": "someone is not a CODEOWNERS owner; choose one of: @owner.",
    }
    assign.assert_not_awaited()


@pytest.mark.asyncio
async def test_assign_human_reviewer_accepts_owner() -> None:
    request = SimpleNamespace(kind="standard", thread_id="thread")
    with (
        patch(
            "agent.tools.request_human_review.parse_github_pr_url",
            return_value=SimpleNamespace(owner="o", repo="r", number=1),
        ),
        patch(
            "agent.tools.request_human_review.HumanReviewRequest.active_for",
            AsyncMock(return_value=request),
        ),
        patch("agent.tools.request_human_review.get_config", return_value={}),
        patch(
            "agent.tools.request_human_review.RunConfig.from_config",
            return_value=SimpleNamespace(thread_id="thread"),
        ),
        patch("agent.tools.request_human_review._repository_refusal", AsyncMock(return_value=None)),
        patch(
            "agent.tools.request_human_review.resolve_github_token",
            AsyncMock(return_value=("token", None)),
        ),
        patch(
            "agent.tools.request_human_review.login_is_codeowner",
            AsyncMock(return_value=(True, {"@owner"}, False)),
        ),
        patch(
            "agent.tools.request_human_review.assign",
            AsyncMock(return_value=SimpleNamespace(success=True)),
        ),
    ):
        result = await assign_human_reviewer("https://github.com/o/r/pull/1", "owner")
    assert result["success"] is True
