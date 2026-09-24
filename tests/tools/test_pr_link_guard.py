from unittest.mock import AsyncMock

import pytest

from agent.tools import pr_link_guard


async def test_recorded_pull_request_link_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pr_link_guard,
        "thread_pull_requests",
        AsyncMock(
            return_value=[
                {
                    "repo_full_name": "langchain-ai/open-swe",
                    "number": 42,
                    "url": "https://github.com/langchain-ai/open-swe/pull/42",
                }
            ]
        ),
    )
    external = AsyncMock()
    monkeypatch.setattr(pr_link_guard, "_github_pr_exists", external)

    assert (
        await pr_link_guard.unverified_pr_links(
            "thread-1", "Delivered: https://github.com/langchain-ai/open-swe/pull/42"
        )
        == []
    )
    external.assert_not_awaited()


async def test_invented_pull_request_number_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pr_link_guard, "thread_pull_requests", AsyncMock(return_value=[]))
    monkeypatch.setattr(pr_link_guard, "_github_pr_exists", AsyncMock(return_value=False))
    url = "https://github.com/langchain-ai/open-swe/pull/999"

    assert await pr_link_guard.unverified_pr_links("thread-1", f"Delivered: {url}") == [url]
    assert url in pr_link_guard.pr_link_error([url])["error"]


async def test_existing_user_pasted_pull_request_link_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pr_link_guard, "thread_pull_requests", AsyncMock(return_value=[]))
    monkeypatch.setattr(pr_link_guard, "_github_pr_exists", AsyncMock(return_value=True))

    assert (
        await pr_link_guard.unverified_pr_links(
            "thread-1", "Existing PR: https://github.com/user/project/pull/7"
        )
        == []
    )


async def test_message_without_pull_request_link_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = AsyncMock()
    external = AsyncMock()
    monkeypatch.setattr(pr_link_guard, "thread_pull_requests", ledger)
    monkeypatch.setattr(pr_link_guard, "_github_pr_exists", external)

    assert await pr_link_guard.unverified_pr_links("thread-1", "No link here") == []
    ledger.assert_not_awaited()
    external.assert_not_awaited()
