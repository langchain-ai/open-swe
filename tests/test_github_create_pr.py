"""Unit tests for create_github_pr HTTP error fallback behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agent.utils.github import create_github_pr


@pytest.mark.asyncio
async def test_create_github_pr_http_error_falls_back_to_existing_pr():
    """When httpx.HTTPError is raised during PR creation, the function should
    fall back to _find_existing_pr and return the existing PR if one is found.

    Guards against regression where transient network errors after a successful
    GitHub PR creation would cause a false 'Failed to create GitHub PR' result.
    """
    existing_pr_url = "https://github.com/owner/repo/pull/42"
    existing_pr_number = 42

    mock_post = AsyncMock(side_effect=httpx.HTTPError("connection reset"))
    mock_find = AsyncMock(return_value=(existing_pr_url, existing_pr_number))

    mock_client = MagicMock()
    mock_client.post = mock_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch("agent.utils.github._find_existing_pr", mock_find),
    ):
        pr_url, pr_number, pr_existing = await create_github_pr(
            repo_owner="owner",
            repo_name="repo",
            github_token="token",
            title="Test PR",
            head_branch="feature/test",
            base_branch="main",
            body="PR body",
        )

    assert pr_url == existing_pr_url
    assert pr_number == existing_pr_number
    assert pr_existing is True
    mock_find.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_github_pr_http_error_returns_none_when_no_existing_pr():
    """When httpx.HTTPError is raised and no existing PR is found, return (None, None, False)."""
    mock_post = AsyncMock(side_effect=httpx.HTTPError("connection reset"))
    mock_find = AsyncMock(return_value=(None, None))

    mock_client = MagicMock()
    mock_client.post = mock_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch("agent.utils.github._find_existing_pr", mock_find),
    ):
        pr_url, pr_number, pr_existing = await create_github_pr(
            repo_owner="owner",
            repo_name="repo",
            github_token="token",
            title="Test PR",
            head_branch="feature/test",
            base_branch="main",
            body="PR body",
        )

    assert pr_url is None
    assert pr_number is None
    assert pr_existing is False


@pytest.mark.asyncio
async def test_create_github_pr_http_error_returns_none_when_find_also_fails():
    """When httpx.HTTPError is raised and _find_existing_pr also raises, return (None, None, False)."""
    mock_post = AsyncMock(side_effect=httpx.HTTPError("connection reset"))
    mock_find = AsyncMock(side_effect=Exception("secondary failure"))

    mock_client = MagicMock()
    mock_client.post = mock_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch("agent.utils.github._find_existing_pr", mock_find),
    ):
        pr_url, pr_number, pr_existing = await create_github_pr(
            repo_owner="owner",
            repo_name="repo",
            github_token="token",
            title="Test PR",
            head_branch="feature/test",
            base_branch="main",
            body="PR body",
        )

    assert pr_url is None
    assert pr_number is None
    assert pr_existing is False
