"""Unit tests for the watch-mode webhook handlers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from agent import webapp


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
        patch(
            "agent.webapp.create_review_check_run",
            new_callable=AsyncMock,
            return_value=42,
        ) as create_check,
        patch(
            "agent.webapp.complete_review_check_run",
            new_callable=AsyncMock,
            return_value=True,
        ) as complete_check,
        patch("agent.webapp.is_thread_active", new_callable=AsyncMock, return_value=False),
        patch("agent.webapp.get_client", return_value=fake_client),
    ):
        await webapp.process_github_push_event(payload)

    fake_client.runs.create.assert_not_called()
    set_metadata.assert_awaited_once()
    assert set_metadata.await_args.kwargs["last_reviewed_sha"] == "newsha"
    # Even without a re-review, a settled check lands on the new head so the
    # review stays visible after the head moves.
    create_check.assert_awaited_once()
    assert create_check.await_args.kwargs["head_sha"] == "newsha"
    complete_check.assert_awaited_once()
    assert complete_check.await_args.kwargs["check_run_id"] == 42
    assert complete_check.await_args.kwargs["conclusion"] == "success"


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
        patch("agent.webapp.cache_github_token_for_thread"),
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
        patch("agent.webapp.cache_github_token_for_thread"),
        patch(
            "agent.webapp.set_reviewer_thread_metadata",
            new_callable=AsyncMock,
        ) as set_meta,
        patch(
            "agent.webapp.create_review_check_run",
            new_callable=AsyncMock,
            return_value=99,
        ) as create_check,
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
    # The live head is persisted to thread metadata so a re-review queued into
    # an in-flight run can resolve it despite the run's frozen config.
    head_sha_writes = [
        c.kwargs.get("head_sha")
        for c in set_meta.await_args_list
        if c.kwargs.get("head_sha") is not None
    ]
    assert "newsha" in head_sha_writes
    # A fresh check run is created on the new head SHA (GitHub only shows
    # checks on the current head), and its id is persisted for settling.
    create_check.assert_awaited_once()
    assert create_check.await_args.kwargs["head_sha"] == "newsha"
    check_id_writes = [
        c.kwargs.get("extra", {}).get("review_check_run_id")
        for c in set_meta.await_args_list
        if "review_check_run_id" in (c.kwargs.get("extra") or {})
    ]
    assert 99 in check_id_writes


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
async def test_reviewer_token_for_repo_public_scopes_by_id() -> None:
    get_token = AsyncMock(return_value=("scoped", "exp"))
    with patch("agent.webapp.get_github_app_installation_token_with_expiry", get_token):
        token, expires = await webapp._reviewer_token_for_repo(
            {"owner": "lc", "name": "repo"}, repo_private=False, repo_id=123
        )
    assert (token, expires) == ("scoped", "exp")
    get_token.assert_awaited_once_with(repository_ids=[123])


@pytest.mark.asyncio
async def test_reviewer_token_for_repo_public_scopes_by_name_without_id() -> None:
    get_token = AsyncMock(return_value=("scoped", "exp"))
    with patch("agent.webapp.get_github_app_installation_token_with_expiry", get_token):
        await webapp._reviewer_token_for_repo(
            {"owner": "lc", "name": "repo"}, repo_private=False, repo_id=None
        )
    get_token.assert_awaited_once_with(repositories=["repo"])


@pytest.mark.asyncio
async def test_reviewer_token_for_repo_private_uses_full_token() -> None:
    get_token = AsyncMock(return_value=("full", "exp"))
    with patch("agent.webapp.get_github_app_installation_token_with_expiry", get_token):
        await webapp._reviewer_token_for_repo(
            {"owner": "lc", "name": "repo"}, repo_private=True, repo_id=123
        )
    get_token.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_reviewer_token_for_repo_unknown_privacy_uses_full_token() -> None:
    get_token = AsyncMock(return_value=("full", "exp"))
    with patch("agent.webapp.get_github_app_installation_token_with_expiry", get_token):
        await webapp._reviewer_token_for_repo(
            {"owner": "lc", "name": "repo"}, repo_private=None, repo_id=123
        )
    get_token.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_push_event_public_repo_uses_scoped_token() -> None:
    payload = _push_payload(ref="refs/heads/feat-x", after="newsha", private=False, repo_id=123)
    pr = {
        "number": 7,
        "html_url": "https://github.com/lc/repo/pull/7",
        "title": "T",
        "head": {"sha": "newsha", "ref": "feat-x"},
        "base": {"sha": "basesha", "ref": "main"},
    }
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    get_token = AsyncMock(return_value=("scoped-token", "exp"))
    cache_token = MagicMock()

    with (
        patch(
            "agent.webapp._is_repo_enabled_for_review", new_callable=AsyncMock, return_value=True
        ),
        patch("agent.webapp.get_github_app_installation_token_with_expiry", get_token),
        patch("agent.webapp._fetch_open_pr_for_branch", new_callable=AsyncMock, return_value=pr),
        patch(
            "agent.webapp._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={"kind": "reviewer", "watch": True},
        ),
        patch(
            "agent.webapp._ensure_thread_exists_for_metadata",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("agent.webapp.cache_github_token_for_thread", cache_token),
        patch("agent.webapp.fetch_pr_review_threads", new_callable=AsyncMock, return_value=[]),
        patch("agent.webapp.reconcile_findings_with_review_threads", new_callable=AsyncMock),
        patch("agent.webapp.set_reviewer_thread_metadata", new_callable=AsyncMock),
        patch("agent.webapp.is_thread_active", new_callable=AsyncMock, return_value=False),
        patch("agent.webapp.get_client", return_value=fake_client),
    ):
        await webapp.process_github_push_event(payload)

    get_token.assert_awaited_once_with(repository_ids=[123])
    _, kwargs = fake_client.runs.create.await_args
    assert kwargs["config"]["configurable"]["repo_private"] is False


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
            "agent.webapp._is_repo_enabled_for_review", new_callable=AsyncMock, return_value=True
        ),
        patch("agent.webapp.get_github_app_installation_token_with_expiry", get_token),
        patch("agent.webapp._fetch_open_pr_for_branch", new_callable=AsyncMock, return_value=pr),
        patch(
            "agent.webapp._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={"kind": "reviewer", "watch": True},
        ),
        patch(
            "agent.webapp._ensure_thread_exists_for_metadata",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("agent.webapp.cache_github_token_for_thread", cache_token),
        patch("agent.webapp.fetch_pr_review_threads", new_callable=AsyncMock, return_value=[]),
        patch("agent.webapp.reconcile_findings_with_review_threads", new_callable=AsyncMock),
        patch("agent.webapp.set_reviewer_thread_metadata", new_callable=AsyncMock),
        patch("agent.webapp.is_thread_active", new_callable=AsyncMock, return_value=False),
        patch("agent.webapp.get_client", return_value=fake_client),
    ):
        await webapp.process_github_push_event(payload)

    assert get_token.await_args_list == [call(), call(repository_ids=[456])]
    _, kwargs = fake_client.runs.create.await_args
    assert kwargs["config"]["configurable"]["repo_private"] is False


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
