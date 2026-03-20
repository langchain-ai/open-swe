"""Tests for agent/utils/github.py."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from agent.utils.github import create_github_pr


def _make_response(status_code: int, json_data: dict) -> MagicMock:
    """Create a mock httpx.Response."""
    mock = MagicMock(spec=httpx.Response)
    mock.status_code = status_code
    mock.json.return_value = json_data
    return mock


def test_create_github_pr_patches_existing_pr_on_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """When GitHub returns 422 (PR already exists), create_github_pr must PATCH
    the existing PR with the new title and body before returning."""
    calls: list[dict] = []

    # POST → 422 (PR already exists)
    post_response = _make_response(
        422,
        {"message": "Validation Failed", "errors": [{"message": "A pull request already exists"}]},
    )

    # GET list → existing PR
    get_response = _make_response(
        200,
        [{"html_url": "https://github.com/owner/repo/pull/42", "number": 42}],
    )

    # PATCH → 200 success
    patch_response = _make_response(
        200,
        {"html_url": "https://github.com/owner/repo/pull/42", "number": 42},
    )

    async def fake_post(url, **kwargs):
        calls.append({"method": "POST", "url": url, "json": kwargs.get("json")})
        return post_response

    async def fake_get(url, **kwargs):
        calls.append({"method": "GET", "url": url, "params": kwargs.get("params")})
        return get_response

    async def fake_patch(url, **kwargs):
        calls.append({"method": "PATCH", "url": url, "json": kwargs.get("json")})
        return patch_response

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = fake_post
    mock_client.get = fake_get
    mock_client.patch = fake_patch
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(httpx, "AsyncClient", lambda: mock_client)

    pr_url, pr_number, pr_existing = asyncio.run(
        create_github_pr(
            repo_owner="owner",
            repo_name="repo",
            github_token="test-token",
            title="fix: update the calendar integration [closes CAL-1]",
            head_branch="open-swe/test-thread-id",
            base_branch="main",
            body="## Description\nUpdates the calendar integration.\n\n## Test Plan\n- [ ] Verify calendar events sync",
        )
    )

    # Verify successful return values
    assert pr_url == "https://github.com/owner/repo/pull/42"
    assert pr_number == 42
    assert pr_existing is True

    # Verify PATCH was called
    patch_calls = [c for c in calls if c["method"] == "PATCH"]
    assert len(patch_calls) == 1, f"Expected 1 PATCH call, got {len(patch_calls)}: {calls}"

    patch_call = patch_calls[0]
    assert "/pulls/42" in patch_call["url"]
    assert patch_call["json"]["title"] == "fix: update the calendar integration [closes CAL-1]"
    assert "## Description" in patch_call["json"]["body"]
    assert "calendar" in patch_call["json"]["body"]


def test_create_github_pr_does_not_patch_on_new_pr() -> None:
    """When GitHub returns 201 (new PR created), no PATCH should be issued."""
    calls: list[dict] = []

    post_response = _make_response(
        201,
        {"html_url": "https://github.com/owner/repo/pull/99", "number": 99},
    )

    async def fake_post(url, **kwargs):
        calls.append({"method": "POST", "url": url})
        return post_response

    async def fake_patch(url, **kwargs):
        calls.append({"method": "PATCH", "url": url})
        return _make_response(200, {})

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = fake_post
    mock_client.patch = fake_patch
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    import httpx as _httpx
    import pytest as _pytest

    # Use a simple monkeypatch via importlib approach — inline test with direct patching
    import agent.utils.github as github_module

    original_async_client = _httpx.AsyncClient

    _httpx.AsyncClient = lambda: mock_client  # type: ignore[assignment]
    try:
        pr_url, pr_number, pr_existing = asyncio.run(
            create_github_pr(
                repo_owner="owner",
                repo_name="repo",
                github_token="test-token",
                title="feat: add new feature",
                head_branch="open-swe/new-thread",
                base_branch="main",
                body="## Description\nA new feature.\n\n## Test Plan\n- [ ] Verify feature works",
            )
        )
    finally:
        _httpx.AsyncClient = original_async_client  # type: ignore[assignment]

    assert pr_url == "https://github.com/owner/repo/pull/99"
    assert pr_number == 99
    assert pr_existing is False

    patch_calls = [c for c in calls if c["method"] == "PATCH"]
    assert len(patch_calls) == 0, f"Expected no PATCH calls for new PR, got: {patch_calls}"
