"""Unit tests for the watch-mode webhook handlers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agent.webhooks import github as github_webhooks


def _pr_close_payload(*, action: str, number: int = 7) -> dict[str, Any]:
    return {
        "action": action,
        "repository": {"owner": {"login": "lc"}, "name": "repo"},
        "pull_request": {"number": number, "head": {"ref": "feat-x"}},
    }


@pytest.mark.asyncio
async def test_pr_close_disables_watch() -> None:
    captured: list[Any] = []

    async def fake_set(thread_id: str, **kwargs: Any) -> None:
        captured.append((thread_id, kwargs))

    with (
        patch(
            "agent.webhooks.common._is_repo_auto_review_enabled",
            new_callable=AsyncMock,
            return_value=False,
        ) as auto_review_enabled,
        patch(
            "agent.webhooks.common._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={"kind": "reviewer", "watch": True},
        ),
        patch("agent.webhooks.common.set_reviewer_thread_metadata", side_effect=fake_set),
    ):
        await github_webhooks.process_github_pr_close(_pr_close_payload(action="closed"))
    auto_review_enabled.assert_not_awaited()
    assert captured and captured[0][1]["watch"] is False


@pytest.mark.asyncio
async def test_pr_reopened_re_enables_watch() -> None:
    captured: list[Any] = []

    async def fake_set(thread_id: str, **kwargs: Any) -> None:
        captured.append((thread_id, kwargs))

    with (
        patch(
            "agent.webhooks.common._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={"kind": "reviewer", "watch": False},
        ),
        patch("agent.webhooks.common.set_reviewer_thread_metadata", side_effect=fake_set),
    ):
        await github_webhooks.process_github_pr_close(_pr_close_payload(action="reopened"))
    assert captured and captured[0][1]["watch"] is True


@pytest.mark.asyncio
async def test_pr_close_skips_non_reviewer_threads() -> None:
    fake_set = AsyncMock()
    with (
        patch(
            "agent.webhooks.common._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={"kind": "agent"},
        ),
        patch("agent.webhooks.common.set_reviewer_thread_metadata", new=fake_set),
    ):
        await github_webhooks.process_github_pr_close(_pr_close_payload(action="closed"))
    fake_set.assert_not_called()
