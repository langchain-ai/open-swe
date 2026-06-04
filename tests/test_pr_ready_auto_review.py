"""Tests for the opened / ready_for_review auto-review webhook handlers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent import webapp


def _pr_payload(
    *,
    action: str,
    draft: bool,
    author: str = "alice",
    private: bool | None = None,
) -> dict[str, Any]:
    repository: dict[str, Any] = {"owner": {"login": "lc"}, "name": "repo", "id": 123}
    if private is not None:
        repository["private"] = private
    return {
        "action": action,
        "repository": repository,
        "pull_request": {
            "number": 7,
            "html_url": "https://github.com/lc/repo/pull/7",
            "title": "T",
            "draft": draft,
            "user": {"login": author},
            "head": {"sha": "headsha", "ref": "feat-x"},
            "base": {"sha": "basesha", "ref": "main"},
        },
        "sender": {"login": author, "id": 1},
    }


def _patch_dispatch_deps(monkeypatch: pytest.MonkeyPatch, fake_client: Any) -> None:
    monkeypatch.setattr(
        webapp,
        "get_github_app_installation_token_with_expiry",
        AsyncMock(return_value=("token", None)),
    )
    monkeypatch.setattr(webapp, "_ensure_thread_exists_for_metadata", AsyncMock(return_value=True))
    monkeypatch.setattr(webapp, "cache_github_token_for_thread", MagicMock())
    monkeypatch.setattr(webapp, "set_reviewer_thread_metadata", AsyncMock())
    monkeypatch.setattr(webapp, "is_thread_active", AsyncMock(return_value=False))
    monkeypatch.setattr(webapp, "get_client", lambda url: fake_client)


@pytest.mark.asyncio
async def test_pr_ready_non_draft_triggers_run(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    _patch_dispatch_deps(monkeypatch, fake_client)
    monkeypatch.setattr(webapp, "get_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(webapp, "get_team_settings", AsyncMock(return_value={}))

    await webapp.process_github_pr_ready(_pr_payload(action="opened", draft=False))

    fake_client.runs.create.assert_awaited_once()
    _, kwargs = fake_client.runs.create.await_args
    assert kwargs["config"]["configurable"]["source"] == "github"
    assert kwargs["config"]["configurable"]["pr_number"] == 7


@pytest.mark.asyncio
async def test_pr_ready_public_repo_uses_scoped_reviewer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    get_token = AsyncMock(return_value=("scoped-token", "expires"))
    monkeypatch.setattr(webapp, "get_github_app_installation_token_with_expiry", get_token)
    monkeypatch.setattr(webapp, "_ensure_thread_exists_for_metadata", AsyncMock(return_value=True))
    cache_token = MagicMock()
    monkeypatch.setattr(webapp, "cache_github_token_for_thread", cache_token)
    monkeypatch.setattr(webapp, "set_reviewer_thread_metadata", AsyncMock())
    monkeypatch.setattr(webapp, "is_thread_active", AsyncMock(return_value=False))
    monkeypatch.setattr(webapp, "get_client", lambda url: fake_client)
    monkeypatch.setattr(webapp, "get_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(webapp, "get_team_settings", AsyncMock(return_value={}))

    await webapp.process_github_pr_ready(_pr_payload(action="opened", draft=False, private=False))

    get_token.assert_awaited_once_with(repository_ids=[123])
    cache_token.assert_called_once()
    assert cache_token.call_args.args[1] == "scoped-token"
    _, kwargs = fake_client.runs.create.await_args
    assert kwargs["config"]["configurable"]["repo_private"] is False


@pytest.mark.asyncio
async def test_pr_ready_private_repo_uses_full_reviewer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    get_token = AsyncMock(return_value=("full-token", "expires"))
    monkeypatch.setattr(webapp, "get_github_app_installation_token_with_expiry", get_token)
    monkeypatch.setattr(webapp, "_ensure_thread_exists_for_metadata", AsyncMock(return_value=True))
    monkeypatch.setattr(webapp, "cache_github_token_for_thread", MagicMock())
    monkeypatch.setattr(webapp, "set_reviewer_thread_metadata", AsyncMock())
    monkeypatch.setattr(webapp, "is_thread_active", AsyncMock(return_value=False))
    monkeypatch.setattr(webapp, "get_client", lambda url: fake_client)
    monkeypatch.setattr(webapp, "get_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(webapp, "get_team_settings", AsyncMock(return_value={}))

    await webapp.process_github_pr_ready(_pr_payload(action="opened", draft=False, private=True))

    get_token.assert_awaited_once_with()
    _, kwargs = fake_client.runs.create.await_args
    assert kwargs["config"]["configurable"]["repo_private"] is True


@pytest.mark.asyncio
async def test_pr_ready_for_review_triggers_run(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    _patch_dispatch_deps(monkeypatch, fake_client)
    monkeypatch.setattr(webapp, "_get_thread_metadata_safe", AsyncMock(return_value=None))
    monkeypatch.setattr(webapp, "get_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(webapp, "get_team_settings", AsyncMock(return_value={}))

    await webapp.process_github_pr_ready(_pr_payload(action="ready_for_review", draft=False))

    fake_client.runs.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_pr_ready_for_review_skips_when_head_already_reviewed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    set_metadata = AsyncMock()
    get_token = AsyncMock(return_value=("token", None))
    monkeypatch.setattr(webapp, "get_github_app_installation_token_with_expiry", get_token)
    monkeypatch.setattr(webapp, "set_reviewer_thread_metadata", set_metadata)
    monkeypatch.setattr(
        webapp,
        "_get_thread_metadata_safe",
        AsyncMock(
            return_value={
                "kind": "reviewer",
                "watch": False,
                "last_reviewed_sha": "headsha",
            }
        ),
    )
    monkeypatch.setattr(webapp, "get_client", lambda url: fake_client)
    monkeypatch.setattr(webapp, "get_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(webapp, "get_team_settings", AsyncMock(return_value={}))

    await webapp.process_github_pr_ready(_pr_payload(action="ready_for_review", draft=False))

    fake_client.runs.create.assert_not_called()
    get_token.assert_not_awaited()
    set_metadata.assert_awaited_once()
    assert set_metadata.await_args.kwargs["watch"] is True


@pytest.mark.asyncio
async def test_pr_ready_for_review_uses_re_review_after_previous_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    _patch_dispatch_deps(monkeypatch, fake_client)
    monkeypatch.setattr(
        webapp,
        "_get_thread_metadata_safe",
        AsyncMock(
            return_value={
                "kind": "reviewer",
                "watch": False,
                "last_reviewed_sha": "oldsha",
            }
        ),
    )
    monkeypatch.setattr(webapp, "get_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(webapp, "get_team_settings", AsyncMock(return_value={}))

    await webapp.process_github_pr_ready(_pr_payload(action="ready_for_review", draft=False))

    fake_client.runs.create.assert_awaited_once()
    _, kwargs = fake_client.runs.create.await_args
    configurable = kwargs["config"]["configurable"]
    assert configurable["re_review"] is True
    assert configurable["last_reviewed_sha"] == "oldsha"
    assert configurable["head_sha"] == "headsha"
    assert "marked ready for review" in kwargs["input"]["messages"][0]["content"]
    head_sha_writes = [
        c.kwargs.get("head_sha")
        for c in webapp.set_reviewer_thread_metadata.await_args_list
        if c.kwargs.get("head_sha") is not None
    ]
    assert "headsha" in head_sha_writes


@pytest.mark.asyncio
async def test_pr_ready_draft_user_override_off_wins_over_team_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    _patch_dispatch_deps(monkeypatch, fake_client)
    monkeypatch.setattr(
        webapp,
        "get_profile",
        AsyncMock(return_value={"login": "alice", "review_draft_prs": False}),
    )
    monkeypatch.setattr(
        webapp, "get_team_settings", AsyncMock(return_value={"review_draft_prs": True})
    )

    await webapp.process_github_pr_ready(_pr_payload(action="opened", draft=True))

    fake_client.runs.create.assert_not_called()


@pytest.mark.asyncio
async def test_pr_ready_draft_user_override_on_wins_over_team_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    _patch_dispatch_deps(monkeypatch, fake_client)
    monkeypatch.setattr(
        webapp,
        "get_profile",
        AsyncMock(return_value={"login": "alice", "review_draft_prs": True}),
    )
    monkeypatch.setattr(
        webapp,
        "get_team_settings",
        AsyncMock(return_value={"review_draft_prs": False}),
    )

    await webapp.process_github_pr_ready(_pr_payload(action="opened", draft=True))

    fake_client.runs.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_pr_ready_draft_user_default_falls_back_to_team_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    _patch_dispatch_deps(monkeypatch, fake_client)
    # User profile exists but review_draft_prs is None — inherit team default.
    monkeypatch.setattr(
        webapp,
        "get_profile",
        AsyncMock(return_value={"login": "alice", "review_draft_prs": None}),
    )
    monkeypatch.setattr(
        webapp, "get_team_settings", AsyncMock(return_value={"review_draft_prs": True})
    )

    await webapp.process_github_pr_ready(_pr_payload(action="opened", draft=True))

    fake_client.runs.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_pr_ready_draft_no_profile_falls_back_to_team_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    _patch_dispatch_deps(monkeypatch, fake_client)
    # External contributor — inherit team default (off).
    monkeypatch.setattr(webapp, "get_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(
        webapp,
        "get_team_settings",
        AsyncMock(return_value={"review_draft_prs": False}),
    )

    await webapp.process_github_pr_ready(_pr_payload(action="opened", draft=True))

    fake_client.runs.create.assert_not_called()


@pytest.mark.asyncio
async def test_pr_ready_draft_no_profile_falls_back_to_team_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    _patch_dispatch_deps(monkeypatch, fake_client)
    monkeypatch.setattr(webapp, "get_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(
        webapp, "get_team_settings", AsyncMock(return_value={"review_draft_prs": True})
    )

    await webapp.process_github_pr_ready(_pr_payload(action="opened", draft=True))

    fake_client.runs.create.assert_awaited_once()


def _converted_to_draft_payload(author: str = "alice") -> dict[str, Any]:
    return {
        "action": "converted_to_draft",
        "repository": {"owner": {"login": "lc"}, "name": "repo"},
        "pull_request": {
            "number": 7,
            "head": {"ref": "feat-x"},
            "user": {"login": author},
        },
    }


@pytest.mark.asyncio
async def test_converted_to_draft_disables_watch_when_drafts_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Any] = []

    async def fake_set(thread_id: str, **kwargs: Any) -> None:
        captured.append((thread_id, kwargs))

    with (
        patch(
            "agent.webapp._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={"kind": "reviewer", "watch": True},
        ),
        patch(
            "agent.webapp.get_profile",
            new_callable=AsyncMock,
            return_value={"login": "alice", "review_draft_prs": False},
        ),
        patch(
            "agent.webapp.get_team_settings",
            new_callable=AsyncMock,
            return_value={"review_draft_prs": False},
        ),
        patch("agent.webapp.set_reviewer_thread_metadata", side_effect=fake_set),
    ):
        await webapp.process_github_pr_close(_converted_to_draft_payload())
    assert captured and captured[0][1]["watch"] is False


@pytest.mark.asyncio
async def test_converted_to_draft_keeps_watch_when_author_drafts_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_set = AsyncMock()
    with (
        patch(
            "agent.webapp._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={"kind": "reviewer", "watch": True},
        ),
        patch(
            "agent.webapp.get_profile",
            new_callable=AsyncMock,
            return_value={"login": "alice", "review_draft_prs": True},
        ),
        patch(
            "agent.webapp.get_team_settings",
            new_callable=AsyncMock,
            return_value={"review_draft_prs": False},
        ),
        patch("agent.webapp.set_reviewer_thread_metadata", new=fake_set),
    ):
        await webapp.process_github_pr_close(_converted_to_draft_payload())
    fake_set.assert_not_called()


@pytest.mark.asyncio
async def test_converted_to_draft_keeps_watch_when_team_default_drafts_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_set = AsyncMock()
    with (
        patch(
            "agent.webapp._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={"kind": "reviewer", "watch": True},
        ),
        # Author inherits team default — team has drafts on.
        patch(
            "agent.webapp.get_profile",
            new_callable=AsyncMock,
            return_value={"login": "alice", "review_draft_prs": None},
        ),
        patch(
            "agent.webapp.get_team_settings",
            new_callable=AsyncMock,
            return_value={"review_draft_prs": True},
        ),
        patch("agent.webapp.set_reviewer_thread_metadata", new=fake_set),
    ):
        await webapp.process_github_pr_close(_converted_to_draft_payload())
    fake_set.assert_not_called()
