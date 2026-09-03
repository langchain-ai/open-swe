"""Unit tests for agent-thread PR-state tracking from PR webhook events."""

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from agent.webhooks import common as webhook_common


@asynccontextmanager
async def _unlocked(*args, **kwargs):
    yield


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
    fake_client.threads.get = AsyncMock(return_value=fake_client.threads.search.return_value[0])
    fake_client.threads.update = AsyncMock()

    with (
        patch("agent.webhooks.common.get_client", return_value=fake_client),
        patch("agent.webhooks.common.agent_thread_pr_state_lock", _unlocked),
    ):
        await webhook_common.update_agent_thread_pr_state(_pr_payload(state="closed"))

    assert fake_client.threads.search.await_count == 2
    fake_client.threads.update.assert_awaited_once()
    call_args = fake_client.threads.update.await_args
    assert call_args is not None
    assert call_args.kwargs["thread_id"] == "t1"
    assert call_args.kwargs["metadata"] == {
        "pr_state": "closed",
        "resolved": True,
        "resolved_at_ms": ANY,
        "auto_resolved_by_prs": True,
    }


@pytest.mark.asyncio
async def test_update_agent_thread_pr_state_skips_reviewer_threads() -> None:
    fake_client = MagicMock()
    fake_client.threads.search = AsyncMock(
        return_value=[{"thread_id": "rev", "metadata": {"kind": "reviewer"}}]
    )
    fake_client.threads.update = AsyncMock()

    with (
        patch("agent.webhooks.common.get_client", return_value=fake_client),
        patch("agent.webhooks.common.agent_thread_pr_state_lock", _unlocked),
    ):
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
    thread = {
        "thread_id": "t1",
        "metadata": {
            "kind": "agent",
            "pr_url": latest_pr["url"],
            "pr_state": "open",
            "pr_urls": [older_pr["url"], latest_pr["url"]],
            "pull_requests": [older_pr, latest_pr],
        },
    }
    fake_client = MagicMock()
    fake_client.threads.search = AsyncMock(side_effect=[[], [thread]])
    fake_client.threads.get = AsyncMock(return_value=thread)
    fake_client.threads.update = AsyncMock()

    with (
        patch("agent.webhooks.common.get_client", return_value=fake_client),
        patch("agent.webhooks.common.agent_thread_pr_state_lock", _unlocked),
    ):
        await webhook_common.update_agent_thread_pr_state(_pr_payload(state="closed"))

    fake_client.threads.update.assert_awaited_once_with(
        thread_id="t1",
        metadata={"pull_requests": [{**older_pr, "state": "closed"}, latest_pr]},
    )


@pytest.mark.asyncio
async def test_update_agent_thread_pr_state_resolves_after_all_prs_close() -> None:
    closing_pr = {
        "repo_full_name": "lc/repo",
        "number": 7,
        "url": "https://github.com/lc/repo/pull/7",
        "state": "open",
    }
    merged_pr = {
        "repo_full_name": "lc/other",
        "number": 9,
        "url": "https://github.com/lc/other/pull/9",
        "state": "merged",
    }
    thread = {
        "thread_id": "t1",
        "metadata": {
            "kind": "agent",
            "pr_url": merged_pr["url"],
            "pr_state": "merged",
            "pr_urls": [closing_pr["url"], merged_pr["url"]],
            "pull_requests": [closing_pr, merged_pr],
        },
    }
    fake_client = MagicMock()
    fake_client.threads.search = AsyncMock(side_effect=[[thread], []])
    fake_client.threads.get = AsyncMock(return_value=thread)
    fake_client.threads.update = AsyncMock()

    with (
        patch("agent.webhooks.common.get_client", return_value=fake_client),
        patch("agent.webhooks.common.agent_thread_pr_state_lock", _unlocked),
    ):
        await webhook_common.update_agent_thread_pr_state(_pr_payload(state="closed"))

    fake_client.threads.update.assert_awaited_once_with(
        thread_id="t1",
        metadata={
            "pull_requests": [{**closing_pr, "state": "closed"}, merged_pr],
            "resolved": True,
            "resolved_at_ms": ANY,
            "auto_resolved_by_prs": True,
        },
    )


@pytest.mark.asyncio
async def test_update_agent_thread_pr_state_reopens_auto_resolved_thread() -> None:
    closed_pr = {
        "repo_full_name": "lc/repo",
        "number": 7,
        "url": "https://github.com/lc/repo/pull/7",
        "state": "closed",
    }
    fake_client = MagicMock()
    fake_client.threads.search = AsyncMock(
        return_value=[
            {
                "thread_id": "t1",
                "metadata": {
                    "kind": "agent",
                    "pr_url": closed_pr["url"],
                    "pr_state": "closed",
                    "pr_urls": [closed_pr["url"]],
                    "pull_requests": [closed_pr],
                    "resolved": True,
                    "resolved_at_ms": 123,
                    "auto_resolved_by_prs": True,
                },
            }
        ]
    )
    fake_client.threads.get = AsyncMock(return_value=fake_client.threads.search.return_value[0])
    fake_client.threads.update = AsyncMock()

    with (
        patch("agent.webhooks.common.get_client", return_value=fake_client),
        patch("agent.webhooks.common.agent_thread_pr_state_lock", _unlocked),
    ):
        await webhook_common.update_agent_thread_pr_state(_pr_payload(state="open"))

    fake_client.threads.update.assert_awaited_once_with(
        thread_id="t1",
        metadata={
            "pull_requests": [{**closed_pr, "state": "open"}],
            "pr_state": "open",
            "resolved": False,
            "resolved_at_ms": None,
            "auto_resolved_by_prs": False,
        },
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
    fake_client.threads.get = AsyncMock(return_value=target)
    fake_client.threads.update = AsyncMock()

    with (
        patch("agent.webhooks.common.get_client", return_value=fake_client),
        patch("agent.webhooks.common.agent_thread_pr_state_lock", _unlocked),
    ):
        await webhook_common.update_agent_thread_pr_state(_pr_payload(state="closed"))

    assert [call.kwargs["offset"] for call in fake_client.threads.search.await_args_list] == [
        0,
        50,
        0,
    ]
    fake_client.threads.update.assert_awaited_once_with(
        thread_id="t51",
        metadata={
            "pr_state": "closed",
            "resolved": True,
            "resolved_at_ms": ANY,
            "auto_resolved_by_prs": True,
        },
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
                    "resolved": True,
                    "auto_resolved_by_prs": True,
                },
            }
        ]
    )
    fake_client.threads.get = AsyncMock(return_value=fake_client.threads.search.return_value[0])
    fake_client.threads.update = AsyncMock()
    fake_client.runs.list = AsyncMock(return_value=[])

    with (
        patch("agent.webhooks.common.get_client", return_value=fake_client),
        patch("agent.webhooks.common.agent_thread_pr_state_lock", _unlocked),
    ):
        await webhook_common.update_agent_thread_pr_state(_pr_payload(state="closed", merged=True))

    fake_client.threads.update.assert_not_called()


@pytest.mark.asyncio
async def test_merged_pr_records_feedback_on_every_thread_run() -> None:
    thread = {
        "thread_id": "t1",
        "metadata": {
            "kind": "agent",
            "pr_url": "https://github.com/lc/repo/pull/7",
            "pr_state": "open",
        },
    }
    fake_client = MagicMock()
    fake_client.threads.search = AsyncMock(return_value=[thread])
    fake_client.threads.get = AsyncMock(return_value=thread)
    fake_client.threads.update = AsyncMock()
    fake_client.runs.list = AsyncMock(return_value=[{"run_id": "run-1"}, {"id": "run-2"}])
    create_feedback = AsyncMock(return_value=True)

    with (
        patch("agent.webhooks.common.get_client", return_value=fake_client),
        patch("agent.webhooks.common.agent_thread_pr_state_lock", _unlocked),
        patch("agent.webhooks.common.create_langsmith_feedback", create_feedback),
    ):
        await webhook_common.update_agent_thread_pr_state(_pr_payload(state="closed", merged=True))

    fake_client.runs.list.assert_awaited_once_with("t1", limit=100, offset=0)
    assert [call.args[0] for call in create_feedback.await_args_list] == ["run-1", "run-2"]
    assert all(call.kwargs["score"] == 1.0 for call in create_feedback.await_args_list)
    assert all(
        call.kwargs["source_info"]
        == {
            "source": "github_pr_merged",
            "thread_id": "t1",
            "pr_url": "https://github.com/lc/repo/pull/7",
        }
        for call in create_feedback.await_args_list
    )


@pytest.mark.asyncio
async def test_closed_unmerged_pr_does_not_record_feedback() -> None:
    thread = {
        "thread_id": "t1",
        "metadata": {
            "kind": "agent",
            "pr_url": "https://github.com/lc/repo/pull/7",
            "pr_state": "open",
        },
    }
    fake_client = MagicMock()
    fake_client.threads.search = AsyncMock(return_value=[thread])
    fake_client.threads.get = AsyncMock(return_value=thread)
    fake_client.threads.update = AsyncMock()
    fake_client.runs.list = AsyncMock()
    create_feedback = AsyncMock()

    with (
        patch("agent.webhooks.common.get_client", return_value=fake_client),
        patch("agent.webhooks.common.agent_thread_pr_state_lock", _unlocked),
        patch("agent.webhooks.common.create_langsmith_feedback", create_feedback),
    ):
        await webhook_common.update_agent_thread_pr_state(_pr_payload(state="closed"))

    fake_client.runs.list.assert_not_called()
    create_feedback.assert_not_called()


@pytest.mark.asyncio
async def test_duplicate_terminal_state_preserves_manual_unresolve() -> None:
    fake_client = MagicMock()
    thread = {
        "thread_id": "t1",
        "metadata": {
            "kind": "agent",
            "pr_url": "https://github.com/lc/repo/pull/7",
            "pr_state": "closed",
            "resolved": False,
            "auto_resolved_by_prs": False,
        },
    }
    fake_client.threads.search = AsyncMock(return_value=[thread])
    fake_client.threads.get = AsyncMock(return_value=thread)
    fake_client.threads.update = AsyncMock()

    with (
        patch("agent.webhooks.common.get_client", return_value=fake_client),
        patch("agent.webhooks.common.agent_thread_pr_state_lock", _unlocked),
    ):
        await webhook_common.update_agent_thread_pr_state(_pr_payload(state="closed"))

    fake_client.threads.update.assert_not_called()


@pytest.mark.asyncio
async def test_update_agent_thread_pr_state_preserves_concurrent_pr_update() -> None:
    first_pr = {
        "repo_full_name": "lc/repo",
        "number": 7,
        "url": "https://github.com/lc/repo/pull/7",
        "state": "open",
    }
    second_pr = {
        "repo_full_name": "lc/other",
        "number": 9,
        "url": "https://github.com/lc/other/pull/9",
        "state": "open",
    }
    stale_thread = {
        "thread_id": "t1",
        "metadata": {"kind": "agent", "pull_requests": [first_pr, second_pr]},
    }
    current_thread = {
        "thread_id": "t1",
        "metadata": {
            "kind": "agent",
            "pull_requests": [first_pr, {**second_pr, "state": "closed"}],
        },
    }
    fake_client = MagicMock()
    fake_client.threads.search = AsyncMock(side_effect=[[stale_thread], []])
    fake_client.threads.get = AsyncMock(return_value=current_thread)
    fake_client.threads.update = AsyncMock()

    with (
        patch("agent.webhooks.common.get_client", return_value=fake_client),
        patch("agent.webhooks.common.agent_thread_pr_state_lock", _unlocked),
    ):
        await webhook_common.update_agent_thread_pr_state(_pr_payload(state="closed"))

    fake_client.threads.get.assert_awaited_once_with("t1")
    fake_client.threads.update.assert_awaited_once_with(
        thread_id="t1",
        metadata={
            "pull_requests": [
                {**first_pr, "state": "closed"},
                {**second_pr, "state": "closed"},
            ],
            "resolved": True,
            "resolved_at_ms": ANY,
            "auto_resolved_by_prs": True,
        },
    )
