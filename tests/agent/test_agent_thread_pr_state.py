"""Unit tests for agent-thread PR-state tracking from PR webhook events."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.webhooks import common as webhook_common


def _pr_payload(*, state: str, merged: bool = False, draft: bool = False) -> dict[str, Any]:
    return {
        "pull_request": {
            "html_url": "https://github.com/lc/repo/pull/7",
            "state": state,
            "merged": merged,
            "draft": draft,
        }
    }


def test_pr_state_from_payload_merged() -> None:
    assert (
        webhook_common._pr_state_from_payload(_pr_payload(state="closed", merged=True)) == "merged"
    )


def test_pr_state_from_payload_closed() -> None:
    assert webhook_common._pr_state_from_payload(_pr_payload(state="closed")) == "closed"


def test_pr_state_from_payload_draft() -> None:
    assert webhook_common._pr_state_from_payload(_pr_payload(state="open", draft=True)) == "draft"


def test_pr_state_from_payload_open() -> None:
    assert webhook_common._pr_state_from_payload(_pr_payload(state="open")) == "open"


def test_pr_state_from_payload_missing_pull_request() -> None:
    assert webhook_common._pr_state_from_payload({}) is None


@pytest.mark.asyncio
async def test_update_agent_thread_pr_state_updates_matching_thread() -> None:
    fake_client = MagicMock()
    fake_client.threads.search = AsyncMock(
        return_value=[
            {
                "thread_id": "t1",
                "metadata": {
                    "kind": "agent",
                    "pr_url": "https://github.com/lc/repo/pull/7",
                    "pr_state": "draft",
                },
            }
        ]
    )
    fake_client.threads.update = AsyncMock()

    with patch("agent.webhooks.common.langgraph_client", return_value=fake_client):
        await webhook_common.update_agent_thread_pr_state(_pr_payload(state="closed"))

    assert fake_client.threads.search.await_count == 2
    fake_client.threads.update.assert_awaited_once()
    call_args = fake_client.threads.update.await_args
    assert call_args is not None
    assert call_args.kwargs["thread_id"] == "t1"
    assert call_args.kwargs["metadata"] == {"pr_state": "closed"}


@pytest.mark.asyncio
async def test_update_agent_thread_pr_state_skips_reviewer_threads() -> None:
    fake_client = MagicMock()
    fake_client.threads.search = AsyncMock(
        return_value=[{"thread_id": "rev", "metadata": {"kind": "reviewer"}}]
    )
    fake_client.threads.update = AsyncMock()

    with patch("agent.webhooks.common.langgraph_client", return_value=fake_client):
        await webhook_common.update_agent_thread_pr_state(_pr_payload(state="closed"))

    fake_client.threads.update.assert_not_called()


@pytest.mark.asyncio
async def test_update_agent_thread_pr_state_updates_non_latest_collection_entry() -> None:
    older_pr = {
        "repo_full_name": "lc/repo",
        "number": 7,
        "url": "https://github.com/lc/repo/pull/7",
        "state": "draft",
    }
    latest_pr = {
        "repo_full_name": "lc/other",
        "number": 9,
        "url": "https://github.com/lc/other/pull/9",
        "state": "open",
    }
    fake_client = MagicMock()
    fake_client.threads.search = AsyncMock(
        side_effect=[
            [],
            [
                {
                    "thread_id": "t1",
                    "metadata": {
                        "kind": "agent",
                        "pr_url": latest_pr["url"],
                        "pr_state": "open",
                        "pr_urls": [older_pr["url"], latest_pr["url"]],
                        "pull_requests": [older_pr, latest_pr],
                    },
                }
            ],
        ]
    )
    fake_client.threads.update = AsyncMock()

    with patch("agent.webhooks.common.langgraph_client", return_value=fake_client):
        await webhook_common.update_agent_thread_pr_state(_pr_payload(state="closed"))

    fake_client.threads.update.assert_awaited_once_with(
        thread_id="t1",
        metadata={"pull_requests": [{**older_pr, "state": "closed"}, latest_pr]},
    )


@pytest.mark.asyncio
async def test_update_agent_thread_pr_state_paginates_all_matching_threads() -> None:
    first_page = [
        {"thread_id": f"reviewer-{index}", "metadata": {"kind": "reviewer"}} for index in range(50)
    ]
    target = {
        "thread_id": "t51",
        "metadata": {
            "kind": "agent",
            "pr_url": "https://github.com/lc/repo/pull/7",
            "pr_state": "draft",
        },
    }
    fake_client = MagicMock()
    fake_client.threads.search = AsyncMock(side_effect=[first_page, [target], []])
    fake_client.threads.update = AsyncMock()

    with patch("agent.webhooks.common.langgraph_client", return_value=fake_client):
        await webhook_common.update_agent_thread_pr_state(_pr_payload(state="closed"))

    assert [call.kwargs["offset"] for call in fake_client.threads.search.await_args_list] == [
        0,
        50,
        0,
    ]
    fake_client.threads.update.assert_awaited_once_with(
        thread_id="t51", metadata={"pr_state": "closed"}
    )


@pytest.mark.asyncio
async def test_update_agent_thread_pr_state_noop_when_state_unchanged() -> None:
    fake_client = MagicMock()
    fake_client.threads.search = AsyncMock(
        return_value=[
            {
                "thread_id": "t1",
                "metadata": {
                    "pr_url": "https://github.com/lc/repo/pull/7",
                    "pr_state": "merged",
                },
            }
        ]
    )
    fake_client.threads.update = AsyncMock()

    with patch("agent.webhooks.common.langgraph_client", return_value=fake_client):
        await webhook_common.update_agent_thread_pr_state(_pr_payload(state="closed", merged=True))

    fake_client.threads.update.assert_not_called()
