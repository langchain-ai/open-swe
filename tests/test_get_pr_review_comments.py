"""Tests for get_pr_review_comments tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture()
def repo_config() -> dict[str, str]:
    return {"owner": "langchain-ai", "name": "open-swe"}


@pytest.fixture()
def mock_langgraph_config(repo_config: dict[str, str]) -> MagicMock:
    config = MagicMock()
    config.get.return_value = {"repo": repo_config}
    return config


def _make_pr_comment(
    body: str = "LGTM",
    login: str = "reviewer",
    created_at: str = "2026-03-01T10:00:00Z",
    comment_id: int = 1,
) -> dict[str, Any]:
    return {
        "body": body,
        "user": {"login": login},
        "created_at": created_at,
        "id": comment_id,
    }


def _make_review_comment(
    body: str = "Nit: rename this",
    login: str = "reviewer",
    created_at: str = "2026-03-01T11:00:00Z",
    comment_id: int = 2,
    path: str = "src/foo.py",
    line: int = 42,
) -> dict[str, Any]:
    return {
        "body": body,
        "user": {"login": login},
        "created_at": created_at,
        "id": comment_id,
        "path": path,
        "line": line,
    }


def _make_review(
    body: str = "Looks good, minor nit",
    login: str = "reviewer",
    submitted_at: str = "2026-03-01T12:00:00Z",
    review_id: int = 3,
) -> dict[str, Any]:
    return {
        "body": body,
        "user": {"login": login},
        "submitted_at": submitted_at,
        "id": review_id,
    }


class TestGetPrReviewComments:
    """Tests for the get_pr_review_comments tool."""

    def test_returns_formatted_comments_from_all_three_sources(
        self, mock_langgraph_config: MagicMock, repo_config: dict[str, str]
    ) -> None:
        from agent.tools.get_pr_review_comments import get_pr_review_comments

        pr_comments = [_make_pr_comment(body="Thread comment", comment_id=10)]
        review_comments = [_make_review_comment(body="Inline comment", comment_id=20)]
        reviews = [_make_review(body="Review body", review_id=30)]

        with (
            patch("agent.tools.get_pr_review_comments.get_config", return_value=mock_langgraph_config),
            patch(
                "agent.tools.get_pr_review_comments.get_github_app_installation_token",
                new_callable=AsyncMock,
                return_value="ghs_token123",
            ),
            patch(
                "agent.tools.get_pr_review_comments._fetch_paginated",
                new_callable=AsyncMock,
                side_effect=[pr_comments, review_comments, reviews],
            ),
        ):
            result = get_pr_review_comments(pr_number=42)

        assert result["success"] is True
        assert "comments" in result
        assert len(result["comments"]) == 3
        bodies = [c["body"] for c in result["comments"]]
        assert "Thread comment" in bodies
        assert "Inline comment" in bodies
        assert "Review body" in bodies

    def test_inline_review_comments_include_path_and_line(
        self, mock_langgraph_config: MagicMock
    ) -> None:
        from agent.tools.get_pr_review_comments import get_pr_review_comments

        review_comments = [_make_review_comment(path="agent/tools/foo.py", line=17)]

        with (
            patch("agent.tools.get_pr_review_comments.get_config", return_value=mock_langgraph_config),
            patch(
                "agent.tools.get_pr_review_comments.get_github_app_installation_token",
                new_callable=AsyncMock,
                return_value="ghs_token123",
            ),
            patch(
                "agent.tools.get_pr_review_comments._fetch_paginated",
                new_callable=AsyncMock,
                side_effect=[[], review_comments, []],
            ),
        ):
            result = get_pr_review_comments(pr_number=42)

        assert result["success"] is True
        assert len(result["comments"]) == 1
        comment = result["comments"][0]
        assert comment["path"] == "agent/tools/foo.py"
        assert comment["line"] == 17
        assert comment["type"] == "review_comment"

    def test_uses_repo_config_from_get_config_when_no_owner_name_provided(
        self, mock_langgraph_config: MagicMock
    ) -> None:
        from agent.tools.get_pr_review_comments import get_pr_review_comments

        with (
            patch("agent.tools.get_pr_review_comments.get_config", return_value=mock_langgraph_config) as mock_cfg,
            patch(
                "agent.tools.get_pr_review_comments.get_github_app_installation_token",
                new_callable=AsyncMock,
                return_value="ghs_token123",
            ),
            patch(
                "agent.tools.get_pr_review_comments._fetch_paginated",
                new_callable=AsyncMock,
                side_effect=[[], [], []],
            ) as mock_fetch,
        ):
            get_pr_review_comments(pr_number=5)

        mock_cfg.assert_called_once()
        # Verify the URLs passed to _fetch_paginated use the configured owner/repo
        called_urls = [call.args[1] for call in mock_fetch.call_args_list]
        assert all("langchain-ai/open-swe" in url for url in called_urls)

    def test_accepts_explicit_repo_owner_and_name(self) -> None:
        from agent.tools.get_pr_review_comments import get_pr_review_comments

        config = MagicMock()
        config.get.return_value = {"repo": {"owner": "other-org", "name": "other-repo"}}

        with (
            patch("agent.tools.get_pr_review_comments.get_config", return_value=config),
            patch(
                "agent.tools.get_pr_review_comments.get_github_app_installation_token",
                new_callable=AsyncMock,
                return_value="ghs_token123",
            ),
            patch(
                "agent.tools.get_pr_review_comments._fetch_paginated",
                new_callable=AsyncMock,
                side_effect=[[], [], []],
            ) as mock_fetch,
        ):
            result = get_pr_review_comments(pr_number=7, repo_owner="explicit-org", repo_name="explicit-repo")

        assert result["success"] is True
        called_urls = [call.args[1] for call in mock_fetch.call_args_list]
        assert all("explicit-org/explicit-repo" in url for url in called_urls)

    def test_handles_auth_failure_gracefully(self, mock_langgraph_config: MagicMock) -> None:
        from agent.tools.get_pr_review_comments import get_pr_review_comments

        with (
            patch("agent.tools.get_pr_review_comments.get_config", return_value=mock_langgraph_config),
            patch(
                "agent.tools.get_pr_review_comments.get_github_app_installation_token",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = get_pr_review_comments(pr_number=42)

        assert result["success"] is False
        assert "token" in result["error"].lower()

    def test_handles_missing_repo_config(self) -> None:
        from agent.tools.get_pr_review_comments import get_pr_review_comments

        config = MagicMock()
        config.get.return_value = {}  # no "repo" key

        with patch("agent.tools.get_pr_review_comments.get_config", return_value=config):
            result = get_pr_review_comments(pr_number=42)

        assert result["success"] is False
        assert "repo" in result["error"].lower()

    def test_skips_reviews_with_empty_body(self, mock_langgraph_config: MagicMock) -> None:
        from agent.tools.get_pr_review_comments import get_pr_review_comments

        reviews = [
            _make_review(body="", review_id=1),
            _make_review(body="Approved", review_id=2),
        ]

        with (
            patch("agent.tools.get_pr_review_comments.get_config", return_value=mock_langgraph_config),
            patch(
                "agent.tools.get_pr_review_comments.get_github_app_installation_token",
                new_callable=AsyncMock,
                return_value="ghs_token123",
            ),
            patch(
                "agent.tools.get_pr_review_comments._fetch_paginated",
                new_callable=AsyncMock,
                side_effect=[[], [], reviews],
            ),
        ):
            result = get_pr_review_comments(pr_number=42)

        assert result["success"] is True
        assert len(result["comments"]) == 1
        assert result["comments"][0]["body"] == "Approved"

    def test_comments_sorted_chronologically(self, mock_langgraph_config: MagicMock) -> None:
        from agent.tools.get_pr_review_comments import get_pr_review_comments

        pr_comments = [_make_pr_comment(body="Third", created_at="2026-03-01T13:00:00Z", comment_id=3)]
        review_comments = [_make_review_comment(body="First", created_at="2026-03-01T10:00:00Z", comment_id=1)]
        reviews = [_make_review(body="Second", submitted_at="2026-03-01T12:00:00Z", review_id=2)]

        with (
            patch("agent.tools.get_pr_review_comments.get_config", return_value=mock_langgraph_config),
            patch(
                "agent.tools.get_pr_review_comments.get_github_app_installation_token",
                new_callable=AsyncMock,
                return_value="ghs_token123",
            ),
            patch(
                "agent.tools.get_pr_review_comments._fetch_paginated",
                new_callable=AsyncMock,
                side_effect=[pr_comments, review_comments, reviews],
            ),
        ):
            result = get_pr_review_comments(pr_number=42)

        assert result["success"] is True
        bodies = [c["body"] for c in result["comments"]]
        assert bodies == ["First", "Second", "Third"]

    def test_returns_total_count(self, mock_langgraph_config: MagicMock) -> None:
        from agent.tools.get_pr_review_comments import get_pr_review_comments

        pr_comments = [_make_pr_comment(comment_id=1), _make_pr_comment(comment_id=2)]

        with (
            patch("agent.tools.get_pr_review_comments.get_config", return_value=mock_langgraph_config),
            patch(
                "agent.tools.get_pr_review_comments.get_github_app_installation_token",
                new_callable=AsyncMock,
                return_value="ghs_token123",
            ),
            patch(
                "agent.tools.get_pr_review_comments._fetch_paginated",
                new_callable=AsyncMock,
                side_effect=[pr_comments, [], []],
            ),
        ):
            result = get_pr_review_comments(pr_number=42)

        assert result["success"] is True
        assert result["total"] == 2
