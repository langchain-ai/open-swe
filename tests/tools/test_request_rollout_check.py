"""A rollout check is recorded on the indexed thread that was asked for it."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from openswe.run_config import RunConfig
from openswe.threads.index import IndexedThread
from openswe.tools.request_rollout_check import request_rollout_check

_WHEN = datetime(2026, 1, 1, tzinfo=UTC)


def _indexed(metadata: dict[str, object]) -> IndexedThread:
    return IndexedThread(
        thread_id="thread-1",
        runtime="mda",
        metadata=metadata,
        status="idle",
        created_at=_WHEN,
        updated_at=_WHEN,
    )


def _recorded(metadata: dict[str, object]) -> tuple[IndexedThread, IndexedThread, AsyncMock]:
    runtime = AsyncMock()
    return _indexed(metadata), _indexed({**metadata, "rollout_check": True}), runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["slack", "dashboard", None])
async def test_request_rollout_check_marks_the_current_thread(source: str | None) -> None:
    cfg = RunConfig.parse(
        {
            "thread_id": "thread-1",
            "source": source,
            "github_login": "octo",
            "slack_thread": {"channel_id": "C1", "thread_ts": "1.2"},
        }
    )
    thread, updated, runtime = _recorded({})
    with (
        patch("openswe.tools.request_rollout_check.RunConfig.from_runtime", return_value=cfg),
        patch("openswe.tools.request_rollout_check.index.get", new_callable=AsyncMock) as get,
        patch("openswe.tools.request_rollout_check.index.update", new_callable=AsyncMock) as update,
        patch("openswe.tools.request_rollout_check.RUNTIMES.copies", return_value=[runtime]),
    ):
        get.return_value = thread
        update.return_value = updated
        result = await request_rollout_check()
    assert result == {"success": True, "rollout_check": True, "subscribed": False}
    flag = {
        "rollout_check": True,
        "rollout_check_context": {
            "slack_thread": {"channel_id": "C1", "thread_ts": "1.2"} if source == "slack" else None,
        },
    }
    update.assert_awaited_once_with("thread-1", flag)
    runtime.update.assert_awaited_once_with("thread-1", flag)


@pytest.mark.asyncio
async def test_request_rollout_check_subscribes_when_the_pull_request_already_merged() -> None:
    metadata = {
        "pr_url": "https://github.com/lc/repo/pull/7",
        "merge_commit_pr_url": "https://github.com/lc/repo/pull/7",
        "merge_commit_sha": "d" * 40,
    }
    thread, updated, runtime = _recorded(metadata)
    with (
        patch(
            "openswe.tools.request_rollout_check.RunConfig.from_runtime",
            return_value=RunConfig(thread_id="thread-1"),
        ),
        patch("openswe.tools.request_rollout_check.index.get", new_callable=AsyncMock) as get,
        patch("openswe.tools.request_rollout_check.index.update", new_callable=AsyncMock) as update,
        patch("openswe.tools.request_rollout_check.RUNTIMES.copies", return_value=[runtime]),
        patch(
            "openswe.tools.request_rollout_check.subscribe_merged_thread",
            new_callable=AsyncMock,
        ) as subscribe,
    ):
        get.return_value = thread
        update.return_value = updated
        result = await request_rollout_check()
    assert result["subscribed"] is True
    subscribe.assert_awaited_once()
    assert subscribe.await_args is not None
    assert subscribe.await_args.kwargs["sha"] == "d" * 40
    assert subscribe.await_args.kwargs["owner"] == "lc"
    assert subscribe.await_args.kwargs["number"] == 7
    assert subscribe.await_args.kwargs["metadata"]["rollout_check"] is True


@pytest.mark.asyncio
async def test_request_rollout_check_does_not_subscribe_to_a_different_pull_request() -> None:
    metadata = {
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
    thread, updated, runtime = _recorded(metadata)
    with (
        patch(
            "openswe.tools.request_rollout_check.RunConfig.from_runtime",
            return_value=RunConfig(thread_id="thread-1"),
        ),
        patch("openswe.tools.request_rollout_check.index.get", new_callable=AsyncMock) as get,
        patch("openswe.tools.request_rollout_check.index.update", new_callable=AsyncMock) as update,
        patch("openswe.tools.request_rollout_check.RUNTIMES.copies", return_value=[runtime]),
        patch(
            "openswe.tools.request_rollout_check.subscribe_merged_thread",
            new_callable=AsyncMock,
        ) as subscribe,
    ):
        get.return_value = thread
        update.return_value = updated
        result = await request_rollout_check()
    assert result["subscribed"] is False
    subscribe.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["get", "update"])
async def test_request_rollout_check_does_not_subscribe_when_the_thread_is_not_indexed(
    missing: str,
) -> None:
    thread, _, runtime = _recorded({})
    with (
        patch(
            "openswe.tools.request_rollout_check.RunConfig.from_runtime",
            return_value=RunConfig(thread_id="thread-1"),
        ),
        patch("openswe.tools.request_rollout_check.index.get", new_callable=AsyncMock) as get,
        patch("openswe.tools.request_rollout_check.index.update", new_callable=AsyncMock) as update,
        patch("openswe.tools.request_rollout_check.RUNTIMES.copies", return_value=[runtime]),
        patch(
            "openswe.tools.request_rollout_check.subscribe_merged_thread",
            new_callable=AsyncMock,
        ) as subscribe,
    ):
        get.return_value = None if missing == "get" else thread
        update.return_value = None
        result = await request_rollout_check()
    assert result == {"success": False, "error": "Thread was not found"}
    subscribe.assert_not_awaited()
    runtime.update.assert_not_awaited()
    if missing == "get":
        update.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_rollout_check_without_a_thread_does_not_write() -> None:
    with (
        patch(
            "openswe.tools.request_rollout_check.RunConfig.from_runtime",
            return_value=RunConfig(),
        ),
        patch("openswe.tools.request_rollout_check.index.get", new_callable=AsyncMock) as get,
    ):
        result = await request_rollout_check()
    assert result == {"success": False, "error": "No thread_id in current run config"}
    get.assert_not_awaited()
