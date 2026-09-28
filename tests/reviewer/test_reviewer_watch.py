"""Unit tests for the watch-mode webhook handlers."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from agent.github import webhook as github_webhooks


def _push_payload(
    *,
    ref: str,
    after: str,
    owner: str = "lc",
    name: str = "repo",
    private: bool | None = None,
    repo_id: int | None = None,
) -> dict[str, Any]:
    repository: dict[str, Any] = {"owner": {"login": owner}, "name": name}
    if private is not None:
        repository["private"] = private
    if repo_id is not None:
        repository["id"] = repo_id
    return {
        "ref": ref,
        "after": after,
        "repository": repository,
        "sender": {"login": "alice", "id": 7},
    }


def _pr_close_payload(*, action: str, number: int = 7) -> dict[str, Any]:
    return {
        "action": action,
        "repository": {"owner": {"login": "lc"}, "name": "repo"},
        "pull_request": {"number": number, "head": {"ref": "feat-x"}},
    }

    # If we got here without crashing and with no other patches needed, the
    # function returned early on the deletion check.


@pytest.mark.asyncio
async def test_push_re_review_targets_pushed_head_and_supersedes_previous_check() -> None:
    payload = _push_payload(ref="refs/heads/feat-x", after="newsha")
    # GitHub's PR API can lag a push and still report the previous head.
    pr = {
        "number": 7,
        "html_url": "https://github.com/lc/repo/pull/7",
        "title": "T",
        "head": {"sha": "stalesha", "ref": "feat-x"},
        "base": {"sha": "basesha", "ref": "main"},
    }
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()

    with (
        patch(
            "agent.webhooks.common.is_repo_auto_review_enabled",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "agent.webhooks.common.get_github_app_installation_token_with_expiry",
            new_callable=AsyncMock,
            return_value=("t", None),
        ),
        patch(
            "agent.webhooks.common.fetch_open_pr_for_branch",
            new_callable=AsyncMock,
            return_value=pr,
        ),
        patch(
            "agent.webhooks.common.get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={
                "kind": "reviewer",
                "watch": True,
                "last_reviewed_sha": "oldsha",
                "review_check_run_id": 41,
                "superseded_review_check_run_ids": [40],
            },
        ),
        patch(
            "agent.webhooks.common._fetch_compare_diff",
            new_callable=AsyncMock,
            side_effect=["old diff", "new diff"],
        ),
        patch(
            "agent.webhooks.common.ensure_thread_exists_for_metadata",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("agent.webhooks.common.cache_github_token_for_thread"),
        patch(
            "agent.webhooks.common.set_reviewer_thread_metadata",
            new_callable=AsyncMock,
        ) as set_meta,
        patch(
            "agent.webhooks.common.create_review_check_run",
            new_callable=AsyncMock,
            return_value=99,
        ) as create_check,
        patch(
            "agent.webhooks.common.complete_review_check_run",
            new_callable=AsyncMock,
            side_effect=lambda **kwargs: kwargs["check_run_id"] != 41,
        ) as complete_check,
        patch("agent.webhooks.common.get_client", return_value=fake_client),
    ):
        await github_webhooks.process_github_push_event(payload)

    assert fake_client.runs.create.await_args is not None
    configurable = fake_client.runs.create.await_args.kwargs["config"]["configurable"]
    assert configurable["head_sha"] == "newsha"
    assert create_check.await_args is not None
    assert create_check.await_args.kwargs["head_sha"] == "newsha"
    closed = {
        c.kwargs["check_run_id"]: c.kwargs["conclusion"] for c in complete_check.await_args_list
    }
    assert closed == {40: "neutral", 41: "neutral"}
    tracked = [
        c.kwargs["extra"]
        for c in set_meta.await_args_list
        if "review_check_run_id" in (c.kwargs.get("extra") or {})
    ]
    assert len(tracked) == 1
    assert tracked[0]["review_check_run_id"] == 99
    # 41's close failed, so it stays queued for the next retry; 40 closed.
    assert tracked[0]["superseded_review_check_run_ids"] == [41]


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
            "agent.webhooks.common.is_repo_auto_review_enabled",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "agent.webhooks.common.get_github_app_installation_token",
            new_callable=AsyncMock,
            return_value="t",
        ),
        patch(
            "agent.webhooks.common.fetch_open_pr_for_branch",
            new_callable=AsyncMock,
            return_value=pr,
        ),
        patch(
            "agent.webhooks.common.get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={
                "kind": "reviewer",
                "watch": True,
                "last_reviewed_sha": "samesha",
            },
        ),
        patch("agent.webhooks.common.get_client", return_value=fake_client),
    ):
        await github_webhooks.process_github_push_event(payload)
    fake_client.runs.create.assert_not_called()


@pytest.mark.asyncio
async def test_push_event_rescopes_token_when_pr_metadata_reveals_public() -> None:
    payload = _push_payload(ref="refs/heads/feat-x", after="newsha")
    pr = {
        "number": 7,
        "html_url": "https://github.com/lc/repo/pull/7",
        "title": "T",
        "head": {"sha": "newsha", "ref": "feat-x"},
        "base": {"sha": "basesha", "ref": "main", "repo": {"private": False, "id": 456}},
    }
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    get_token = AsyncMock(side_effect=[("full-token", "e1"), ("scoped-token", "e2")])
    cache_token = MagicMock()

    with (
        patch(
            "agent.webhooks.common.is_repo_auto_review_enabled",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("agent.webhooks.common.get_github_app_installation_token_with_expiry", get_token),
        patch(
            "agent.webhooks.common.fetch_open_pr_for_branch",
            new_callable=AsyncMock,
            return_value=pr,
        ),
        patch(
            "agent.webhooks.common.get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={"kind": "reviewer", "watch": True},
        ),
        patch(
            "agent.webhooks.common.ensure_thread_exists_for_metadata",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("agent.webhooks.common.cache_github_token_for_thread", cache_token),
        patch(
            "agent.webhooks.common.fetch_pr_review_threads", new_callable=AsyncMock, return_value=[]
        ),
        patch(
            "agent.webhooks.common.reconcile_findings_with_review_threads", new_callable=AsyncMock
        ),
        patch("agent.webhooks.common.set_reviewer_thread_metadata", new_callable=AsyncMock),
        patch("agent.webhooks.common.get_client", return_value=fake_client),
    ):
        await github_webhooks.process_github_push_event(payload)

    assert get_token.await_args_list == [call(), call(repository_ids=[456])]
    assert fake_client.runs.create.await_args is not None
    _, kwargs = fake_client.runs.create.await_args
    assert kwargs["config"]["configurable"]["repo_private"] is False


@pytest.mark.asyncio
async def test_pr_close_disables_watch() -> None:
    captured: list[Any] = []

    async def fake_set(thread_id: str, **kwargs: Any) -> None:
        captured.append((thread_id, kwargs))

    with (
        patch(
            "agent.webhooks.common.is_repo_auto_review_enabled",
            new_callable=AsyncMock,
            return_value=False,
        ) as auto_review_enabled,
        patch(
            "agent.webhooks.common.get_thread_metadata_safe",
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
            "agent.webhooks.common.get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={"kind": "reviewer", "watch": False},
        ),
        patch("agent.webhooks.common.set_reviewer_thread_metadata", side_effect=fake_set),
    ):
        await github_webhooks.process_github_pr_close(_pr_close_payload(action="reopened"))
    assert captured and captured[0][1]["watch"] is True
