"""A rollout check is recorded on the thread that was asked for it."""

from unittest.mock import AsyncMock, patch

import pytest

from openswe.run_config import RunConfig
from openswe.tools.request_rollout_check import request_rollout_check


@pytest.mark.asyncio
async def test_request_rollout_check_marks_the_current_thread() -> None:
    client = AsyncMock()
    client.threads.get.return_value = {"metadata": {}}
    with (
        patch(
            "openswe.tools.request_rollout_check.RunConfig.from_runtime",
            return_value=RunConfig(thread_id="thread-1"),
        ),
        patch(
            "openswe.tools.request_rollout_check.langgraph_client",
            return_value=client,
        ),
    ):
        result = await request_rollout_check()
    assert result == {"success": True, "rollout_check": True, "subscribed": False}
    client.threads.update.assert_awaited_once_with(
        thread_id="thread-1",
        metadata={"rollout_check": True},
    )


@pytest.mark.asyncio
async def test_request_rollout_check_subscribes_when_the_pull_request_already_merged() -> None:
    client = AsyncMock()
    client.threads.get.return_value = {
        "metadata": {
            "pr_url": "https://github.com/lc/repo/pull/7",
            "merge_commit_pr_url": "https://github.com/lc/repo/pull/7",
            "merge_commit_sha": "d" * 40,
        }
    }
    with (
        patch(
            "openswe.tools.request_rollout_check.RunConfig.from_runtime",
            return_value=RunConfig(thread_id="thread-1"),
        ),
        patch(
            "openswe.tools.request_rollout_check.langgraph_client",
            return_value=client,
        ),
        patch(
            "openswe.tools.request_rollout_check.subscribe_merged_thread",
            new_callable=AsyncMock,
        ) as subscribe,
    ):
        result = await request_rollout_check()
    assert result["subscribed"] is True
    subscribe.assert_awaited_once()
    assert subscribe.await_args is not None
    assert subscribe.await_args.kwargs["sha"] == "d" * 40
    assert subscribe.await_args.kwargs["owner"] == "lc"
    assert subscribe.await_args.kwargs["number"] == 7


@pytest.mark.asyncio
async def test_request_rollout_check_does_not_subscribe_to_a_different_pull_request() -> None:
    client = AsyncMock()
    client.threads.get.return_value = {
        "metadata": {
            "pr_url": "https://github.com/lc/repo/pull/8",
            "merge_commit_pr_url": "https://github.com/lc/repo/pull/7",
            "merge_commit_sha": "d" * 40,
            "pull_requests": [
                {"url": "https://github.com/lc/repo/pull/8", "state": "open"},
                {
                    "url": "https://github.com/lc/repo/pull/7",
                    "state": "merged",
                    "merge_commit_sha": "d" * 40,
                },
            ],
        }
    }
    with (
        patch(
            "openswe.tools.request_rollout_check.RunConfig.from_runtime",
            return_value=RunConfig(thread_id="thread-1"),
        ),
        patch(
            "openswe.tools.request_rollout_check.langgraph_client",
            return_value=client,
        ),
        patch(
            "openswe.tools.request_rollout_check.subscribe_merged_thread",
            new_callable=AsyncMock,
        ) as subscribe,
    ):
        result = await request_rollout_check()
    assert result["subscribed"] is False
    subscribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_rollout_check_without_a_thread_does_not_write() -> None:
    with patch(
        "openswe.tools.request_rollout_check.RunConfig.from_runtime",
        return_value=RunConfig(),
    ):
        result = await request_rollout_check()
    assert result == {"success": False, "error": "No thread_id in current run config"}
