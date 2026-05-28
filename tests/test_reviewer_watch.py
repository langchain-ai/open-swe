"""Unit tests for the watch-mode webhook handlers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent import webapp


def _push_payload(*, ref: str, after: str, owner: str = "lc", name: str = "repo") -> dict[str, Any]:
    return {
        "ref": ref,
        "after": after,
        "repository": {"owner": {"login": owner}, "name": name},
        "sender": {"login": "alice", "id": 7},
    }


def _pr_close_payload(*, action: str, number: int = 7) -> dict[str, Any]:
    return {
        "action": action,
        "repository": {"owner": {"login": "lc"}, "name": "repo"},
        "pull_request": {"number": number, "head": {"ref": "feat-x"}},
    }


@pytest.mark.asyncio
async def test_push_event_skips_branch_deletion() -> None:
    payload = _push_payload(
        ref="refs/heads/feat-x", after="0000000000000000000000000000000000000000"
    )
    with patch(
        "agent.webapp._is_repo_enabled_for_review", new_callable=AsyncMock, return_value=True
    ):
        await webapp.process_github_push_event(payload)
    # If we got here without crashing and with no other patches needed, the
    # function returned early on the deletion check.


@pytest.mark.asyncio
async def test_push_event_skips_when_thread_not_watching() -> None:
    payload = _push_payload(ref="refs/heads/feat-x", after="newsha")
    pr = {
        "number": 7,
        "html_url": "https://github.com/lc/repo/pull/7",
        "title": "T",
        "head": {"sha": "newsha", "ref": "feat-x"},
        "base": {"sha": "basesha", "ref": "main"},
    }
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()

    with (
        patch(
            "agent.webapp._is_repo_enabled_for_review", new_callable=AsyncMock, return_value=True
        ),
        patch(
            "agent.webapp.get_github_app_installation_token",
            new_callable=AsyncMock,
            return_value="t",
        ),
        patch(
            "agent.webapp._fetch_open_pr_for_branch",
            new_callable=AsyncMock,
            return_value=pr,
        ),
        patch(
            "agent.webapp._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={"kind": "reviewer", "watch": False},
        ),
        patch("agent.webapp.get_client", return_value=fake_client),
    ):
        await webapp.process_github_push_event(payload)
    fake_client.runs.create.assert_not_called()


@pytest.mark.asyncio
async def test_push_event_skips_when_pr_diff_unchanged_since_last_review() -> None:
    payload = _push_payload(ref="refs/heads/feat-x", after="newsha")
    pr = {
        "number": 7,
        "html_url": "https://github.com/lc/repo/pull/7",
        "title": "T",
        "head": {"sha": "newsha", "ref": "feat-x"},
        "base": {"sha": "basesha", "ref": "main"},
    }
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    set_metadata = AsyncMock()

    with (
        patch(
            "agent.webapp._is_repo_enabled_for_review", new_callable=AsyncMock, return_value=True
        ),
        patch(
            "agent.webapp.get_github_app_installation_token_with_expiry",
            new_callable=AsyncMock,
            return_value=("t", None),
        ),
        patch(
            "agent.webapp._fetch_open_pr_for_branch",
            new_callable=AsyncMock,
            return_value=pr,
        ),
        patch(
            "agent.webapp._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={
                "kind": "reviewer",
                "watch": True,
                "last_reviewed_sha": "oldsha",
            },
        ),
        patch(
            "agent.webapp._fetch_compare_diff",
            new_callable=AsyncMock,
            side_effect=["same diff", "same diff"],
        ),
        patch("agent.webapp.set_reviewer_thread_metadata", new=set_metadata),
        patch("agent.webapp.is_thread_active", new_callable=AsyncMock, return_value=False),
        patch("agent.webapp.get_client", return_value=fake_client),
    ):
        await webapp.process_github_push_event(payload)

    fake_client.runs.create.assert_not_called()
    set_metadata.assert_awaited_once()
    assert set_metadata.await_args.kwargs["last_reviewed_sha"] == "newsha"


@pytest.mark.asyncio
async def test_push_event_queues_when_thread_active_even_if_pr_diff_unchanged() -> None:
    payload = _push_payload(ref="refs/heads/feat-x", after="newsha")
    pr = {
        "number": 7,
        "html_url": "https://github.com/lc/repo/pull/7",
        "title": "T",
        "head": {"sha": "newsha", "ref": "feat-x"},
        "base": {"sha": "basesha", "ref": "main"},
    }
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    fetch_compare_diff = AsyncMock()
    queue_message = AsyncMock()

    with (
        patch(
            "agent.webapp._is_repo_enabled_for_review", new_callable=AsyncMock, return_value=True
        ),
        patch(
            "agent.webapp.get_github_app_installation_token_with_expiry",
            new_callable=AsyncMock,
            return_value=("t", None),
        ),
        patch(
            "agent.webapp._fetch_open_pr_for_branch",
            new_callable=AsyncMock,
            return_value=pr,
        ),
        patch(
            "agent.webapp._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={
                "kind": "reviewer",
                "watch": True,
                "last_reviewed_sha": "oldsha",
            },
        ),
        patch("agent.webapp._fetch_compare_diff", new=fetch_compare_diff),
        patch(
            "agent.webapp._ensure_thread_exists_for_metadata",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "agent.webapp.persist_encrypted_github_token",
            new_callable=AsyncMock,
            return_value="enc",
        ),
        patch("agent.webapp.set_reviewer_thread_metadata", new_callable=AsyncMock),
        patch("agent.webapp.is_thread_active", new_callable=AsyncMock, return_value=True),
        patch("agent.webapp.queue_message_for_thread", new=queue_message),
        patch("agent.webapp.get_client", return_value=fake_client),
    ):
        await webapp.process_github_push_event(payload)

    fetch_compare_diff.assert_not_called()
    fake_client.runs.create.assert_not_called()
    queue_message.assert_awaited_once()
    assert "newsha" in queue_message.await_args.args[1]


@pytest.mark.asyncio
async def test_push_event_triggers_re_review_run_when_watching() -> None:
    payload = _push_payload(ref="refs/heads/feat-x", after="newsha")
    pr = {
        "number": 7,
        "html_url": "https://github.com/lc/repo/pull/7",
        "title": "T",
        "head": {"sha": "newsha", "ref": "feat-x"},
        "base": {"sha": "basesha", "ref": "main"},
    }
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()

    with (
        patch(
            "agent.webapp._is_repo_enabled_for_review", new_callable=AsyncMock, return_value=True
        ),
        patch(
            "agent.webapp.get_github_app_installation_token",
            new_callable=AsyncMock,
            return_value="t",
        ),
        patch(
            "agent.webapp.get_github_app_installation_token_with_expiry",
            new_callable=AsyncMock,
            return_value=("t", None),
        ),
        patch(
            "agent.webapp._fetch_open_pr_for_branch",
            new_callable=AsyncMock,
            return_value=pr,
        ),
        patch(
            "agent.webapp._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={
                "kind": "reviewer",
                "watch": True,
                "last_reviewed_sha": "oldsha",
            },
        ),
        patch(
            "agent.webapp._fetch_compare_diff",
            new_callable=AsyncMock,
            side_effect=["old diff", "new diff"],
        ),
        patch(
            "agent.webapp._ensure_thread_exists_for_metadata",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "agent.webapp.persist_encrypted_github_token",
            new_callable=AsyncMock,
            return_value="enc",
        ),
        patch(
            "agent.webapp.set_reviewer_thread_metadata",
            new_callable=AsyncMock,
        ),
        patch("agent.webapp.is_thread_active", new_callable=AsyncMock, return_value=False),
        patch("agent.webapp.get_client", return_value=fake_client),
    ):
        await webapp.process_github_push_event(payload)

    fake_client.runs.create.assert_awaited_once()
    args, kwargs = fake_client.runs.create.await_args
    assert args[1] == "reviewer"
    configurable = kwargs["config"]["configurable"]
    assert configurable["re_review"] is True
    assert configurable["last_reviewed_sha"] == "oldsha"
    assert configurable["head_sha"] == "newsha"


@pytest.mark.asyncio
async def test_push_event_idempotent_when_head_unchanged() -> None:
    payload = _push_payload(ref="refs/heads/feat-x", after="samesha")
    pr = {
        "number": 7,
        "html_url": "https://github.com/lc/repo/pull/7",
        "title": "T",
        "head": {"sha": "samesha", "ref": "feat-x"},
        "base": {"sha": "basesha", "ref": "main"},
    }
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()

    with (
        patch(
            "agent.webapp._is_repo_enabled_for_review", new_callable=AsyncMock, return_value=True
        ),
        patch(
            "agent.webapp.get_github_app_installation_token",
            new_callable=AsyncMock,
            return_value="t",
        ),
        patch(
            "agent.webapp._fetch_open_pr_for_branch",
            new_callable=AsyncMock,
            return_value=pr,
        ),
        patch(
            "agent.webapp._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={
                "kind": "reviewer",
                "watch": True,
                "last_reviewed_sha": "samesha",
            },
        ),
        patch("agent.webapp.get_client", return_value=fake_client),
    ):
        await webapp.process_github_push_event(payload)
    fake_client.runs.create.assert_not_called()


@pytest.mark.asyncio
async def test_pr_close_disables_watch() -> None:
    captured: list[Any] = []

    async def fake_set(thread_id: str, **kwargs: Any) -> None:
        captured.append((thread_id, kwargs))

    with (
        patch(
            "agent.webapp._is_repo_enabled_for_review", new_callable=AsyncMock, return_value=True
        ),
        patch(
            "agent.webapp._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={"kind": "reviewer", "watch": True},
        ),
        patch("agent.webapp.set_reviewer_thread_metadata", side_effect=fake_set),
    ):
        await webapp.process_github_pr_close(_pr_close_payload(action="closed"))
    assert captured and captured[0][1]["watch"] is False


@pytest.mark.asyncio
async def test_pr_reopened_re_enables_watch() -> None:
    captured: list[Any] = []

    async def fake_set(thread_id: str, **kwargs: Any) -> None:
        captured.append((thread_id, kwargs))

    with (
        patch(
            "agent.webapp._is_repo_enabled_for_review", new_callable=AsyncMock, return_value=True
        ),
        patch(
            "agent.webapp._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={"kind": "reviewer", "watch": False},
        ),
        patch("agent.webapp.set_reviewer_thread_metadata", side_effect=fake_set),
    ):
        await webapp.process_github_pr_close(_pr_close_payload(action="reopened"))
    assert captured and captured[0][1]["watch"] is True


@pytest.mark.asyncio
async def test_pr_close_skips_non_reviewer_threads() -> None:
    fake_set = AsyncMock()
    with (
        patch(
            "agent.webapp._is_repo_enabled_for_review", new_callable=AsyncMock, return_value=True
        ),
        patch(
            "agent.webapp._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={"kind": "agent"},
        ),
        patch("agent.webapp.set_reviewer_thread_metadata", new=fake_set),
    ):
        await webapp.process_github_pr_close(_pr_close_payload(action="closed"))
    fake_set.assert_not_called()
