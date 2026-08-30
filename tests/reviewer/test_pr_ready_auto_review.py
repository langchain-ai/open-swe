"""Tests for the opened / ready_for_review auto-review webhook handlers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.webhooks import common as webhook_common
from agent.webhooks import github as github_webhooks


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


def _patch_current_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        webhook_common,
        "fetch_github_pr_metadata",
        AsyncMock(return_value=_pr_payload(action="opened", draft=False)["pull_request"]),
    )


def _patch_dispatch_deps(monkeypatch: pytest.MonkeyPatch, fake_client: MagicMock) -> AsyncMock:
    monkeypatch.setattr(
        webhook_common,
        "get_github_app_installation_token_with_expiry",
        AsyncMock(return_value=("token", None)),
    )
    monkeypatch.setattr(
        webhook_common, "_ensure_thread_exists_for_metadata", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(webhook_common, "cache_github_token_for_thread", MagicMock())
    _patch_current_pr(monkeypatch)
    monkeypatch.setattr(
        webhook_common,
        "_get_thread_metadata_safe",
        AsyncMock(return_value={"kind": "reviewer", "watch": True, "head_sha": "headsha"}),
    )
    set_metadata = AsyncMock()
    monkeypatch.setattr(webhook_common, "set_reviewer_thread_metadata", set_metadata)
    monkeypatch.setattr(webhook_common, "get_client", lambda url: fake_client)
    return set_metadata


@pytest.mark.asyncio
async def test_pr_ready_first_review_clears_finding_reply_for_adversarial_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpointed_config = {"reviewer_event": "finding_reply"}

    async def create_run(*args: Any, **kwargs: Any) -> dict[str, str]:
        checkpointed_config.update(kwargs["config"]["configurable"])
        return {"run_id": "run-1"}

    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock(side_effect=create_run)
    _patch_dispatch_deps(monkeypatch, fake_client)
    selector = AsyncMock(wraps=webhook_common.reviewer_assistant_for_dispatch)
    monkeypatch.setattr(webhook_common, "reviewer_assistant_for_dispatch", selector)
    monkeypatch.setattr(webhook_common, "get_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(
        webhook_common,
        "get_team_settings",
        AsyncMock(return_value={"reviewer_routing": "reviewer_adversarial"}),
    )

    await github_webhooks.process_github_pr_ready(_pr_payload(action="opened", draft=False))

    fake_client.runs.create.assert_awaited_once()
    assert fake_client.runs.create.await_args is not None
    args, kwargs = fake_client.runs.create.await_args
    assert args[1] == "reviewer_adversarial"
    selector.assert_awaited_once_with(
        re_review=False,
        finding_reply=False,
        explicit_request=False,
    )
    assert kwargs["config"]["configurable"]["source"] == "github"
    assert kwargs["config"]["configurable"]["pr_number"] == 7
    assert kwargs["config"]["configurable"]["reviewer_event"] == ""
    assert checkpointed_config["reviewer_event"] == ""


@pytest.mark.asyncio
async def test_pr_ready_public_repo_uses_scoped_reviewer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    get_token = AsyncMock(return_value=("scoped-token", "expires"))
    monkeypatch.setattr(webhook_common, "get_github_app_installation_token_with_expiry", get_token)
    monkeypatch.setattr(
        webhook_common, "_ensure_thread_exists_for_metadata", AsyncMock(return_value=True)
    )
    cache_token = MagicMock()
    monkeypatch.setattr(webhook_common, "cache_github_token_for_thread", cache_token)
    _patch_current_pr(monkeypatch)
    monkeypatch.setattr(
        webhook_common,
        "_get_thread_metadata_safe",
        AsyncMock(return_value={"kind": "reviewer", "watch": True, "head_sha": "headsha"}),
    )
    monkeypatch.setattr(webhook_common, "set_reviewer_thread_metadata", AsyncMock())
    monkeypatch.setattr(webhook_common, "get_client", lambda url: fake_client)
    monkeypatch.setattr(webhook_common, "get_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(webhook_common, "get_team_settings", AsyncMock(return_value={}))

    await github_webhooks.process_github_pr_ready(
        _pr_payload(action="opened", draft=False, private=False)
    )

    get_token.assert_awaited_once_with(target_repo="lc/repo", repository_ids=[123])
    assert fake_client.runs.create.await_args is not None
    _, kwargs = fake_client.runs.create.await_args
    assert kwargs["config"]["configurable"]["repo_private"] is False


@pytest.mark.asyncio
async def test_pr_ready_private_repo_uses_scoped_reviewer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    get_token = AsyncMock(return_value=("full-token", "expires"))
    monkeypatch.setattr(webhook_common, "get_github_app_installation_token_with_expiry", get_token)
    monkeypatch.setattr(
        webhook_common, "_ensure_thread_exists_for_metadata", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(webhook_common, "cache_github_token_for_thread", MagicMock())
    _patch_current_pr(monkeypatch)
    monkeypatch.setattr(
        webhook_common,
        "_get_thread_metadata_safe",
        AsyncMock(return_value={"kind": "reviewer", "watch": True, "head_sha": "headsha"}),
    )
    monkeypatch.setattr(webhook_common, "set_reviewer_thread_metadata", AsyncMock())
    monkeypatch.setattr(webhook_common, "get_client", lambda url: fake_client)
    monkeypatch.setattr(webhook_common, "get_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(webhook_common, "get_team_settings", AsyncMock(return_value={}))

    await github_webhooks.process_github_pr_ready(
        _pr_payload(action="opened", draft=False, private=True)
    )

    get_token.assert_awaited_once_with(target_repo="lc/repo", repository_ids=[123])
    assert fake_client.runs.create.await_args is not None
    _, kwargs = fake_client.runs.create.await_args
    assert kwargs["config"]["configurable"]["repo_private"] is True


@pytest.mark.asyncio
async def test_first_review_does_not_dispatch_stale_head_when_refresh_fails(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    set_metadata = _patch_dispatch_deps(monkeypatch, fake_client)
    monkeypatch.setattr(webhook_common, "fetch_github_pr_metadata", AsyncMock(return_value=None))
    create_check = AsyncMock(return_value=88)
    monkeypatch.setattr(webhook_common, "create_review_check_run", create_check)

    with caplog.at_level("WARNING"):
        await github_webhooks.process_github_pr_ready(_pr_payload(action="opened", draft=False))

    create_check.assert_awaited_once()
    fake_client.runs.create.assert_awaited_once()
    set_metadata.assert_awaited()
    assert fake_client.runs.create.await_args is not None
    assert (
        fake_client.runs.create.await_args.kwargs["config"]["configurable"]["head_sha"] == "headsha"
    )
    assert (
        "lc/repo#7 head=headsha could not refresh PR metadata; using webhook payload" in caplog.text
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata_result",
    [RuntimeError("metadata unavailable"), {"kind": "reviewer", "watch": False}],
)
async def test_first_review_creates_no_check_when_predispatch_confirmation_stands_down(
    metadata_result: dict[str, Any] | Exception,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    _patch_dispatch_deps(monkeypatch, fake_client)
    get_metadata = AsyncMock()
    if isinstance(metadata_result, Exception):
        get_metadata.side_effect = metadata_result
    else:
        get_metadata.return_value = metadata_result
    monkeypatch.setattr(webhook_common, "_get_thread_metadata_safe", get_metadata)
    create_check = AsyncMock()
    monkeypatch.setattr(webhook_common, "create_review_check_run", create_check)

    await github_webhooks.process_github_pr_ready(_pr_payload(action="opened", draft=False))

    create_check.assert_not_awaited()
    fake_client.runs.create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", [{"state": "closed"}, {"draft": True}])
async def test_first_review_honors_lifecycle_change_during_refresh(
    transition: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _pr_payload(action="opened", draft=False)
    current_pr = {**payload["pull_request"], **transition}
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    set_metadata = _patch_dispatch_deps(monkeypatch, fake_client)
    monkeypatch.setattr(
        webhook_common, "fetch_github_pr_metadata", AsyncMock(return_value=current_pr)
    )
    monkeypatch.setattr(
        webhook_common, "_draft_review_enabled_for_author", AsyncMock(return_value=False)
    )
    create_check = AsyncMock()
    monkeypatch.setattr(webhook_common, "create_review_check_run", create_check)

    await github_webhooks.process_github_pr_ready(payload)

    create_check.assert_not_awaited()
    fake_client.runs.create.assert_not_awaited()
    assert any(call.kwargs.get("watch") is False for call in set_metadata.await_args_list)


@pytest.mark.asyncio
async def test_pr_ready_for_review_triggers_run(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    _patch_dispatch_deps(monkeypatch, fake_client)
    monkeypatch.setattr(
        webhook_common,
        "_get_thread_metadata_safe",
        AsyncMock(
            side_effect=[
                None,
                {"kind": "reviewer", "watch": True, "head_sha": "headsha"},
            ]
        ),
    )
    monkeypatch.setattr(webhook_common, "get_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(webhook_common, "get_team_settings", AsyncMock(return_value={}))

    await github_webhooks.process_github_pr_ready(
        _pr_payload(action="ready_for_review", draft=False)
    )

    fake_client.runs.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_pr_ready_for_review_skips_when_head_already_reviewed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    set_metadata = _patch_dispatch_deps(monkeypatch, fake_client)
    get_token = AsyncMock(return_value=("token", None))
    monkeypatch.setattr(webhook_common, "get_github_app_installation_token_with_expiry", get_token)
    monkeypatch.setattr(
        webhook_common,
        "_get_thread_metadata_safe",
        AsyncMock(
            return_value={
                "kind": "reviewer",
                "watch": False,
                "last_reviewed_sha": "headsha",
            }
        ),
    )
    monkeypatch.setattr(webhook_common, "get_client", lambda url: fake_client)
    monkeypatch.setattr(webhook_common, "get_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(webhook_common, "get_team_settings", AsyncMock(return_value={}))

    await github_webhooks.process_github_pr_ready(
        _pr_payload(action="ready_for_review", draft=False)
    )

    fake_client.runs.create.assert_not_called()
    get_token.assert_awaited_once()
    assert len(set_metadata.await_args_list) == 2
    assert all(call.kwargs["watch"] is True for call in set_metadata.await_args_list)
    assert set_metadata.await_args_list[-1].kwargs["head_sha"] == "headsha"


@pytest.mark.asyncio
async def test_pr_ready_for_review_uses_re_review_after_previous_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    set_metadata = _patch_dispatch_deps(monkeypatch, fake_client)
    selector = AsyncMock(wraps=webhook_common.reviewer_assistant_for_dispatch)
    monkeypatch.setattr(webhook_common, "reviewer_assistant_for_dispatch", selector)
    monkeypatch.setattr(webhook_common, "create_review_check_run", AsyncMock(return_value=77))
    monkeypatch.setattr(
        webhook_common,
        "_get_thread_metadata_safe",
        AsyncMock(
            side_effect=[
                {
                    "kind": "reviewer",
                    "watch": False,
                    "last_reviewed_sha": "oldsha",
                },
                {
                    "kind": "reviewer",
                    "watch": True,
                    "head_sha": "headsha",
                },
            ]
        ),
    )
    monkeypatch.setattr(webhook_common, "get_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(
        webhook_common,
        "get_team_settings",
        AsyncMock(return_value={"reviewer_routing": "reviewer_adversarial"}),
    )

    await github_webhooks.process_github_pr_ready(
        _pr_payload(action="ready_for_review", draft=False)
    )

    fake_client.runs.create.assert_awaited_once()
    assert fake_client.runs.create.await_args is not None
    args, kwargs = fake_client.runs.create.await_args
    assert args[1] == "reviewer"
    selector.assert_awaited_once_with(
        re_review=True,
        finding_reply=False,
        explicit_request=False,
    )
    configurable = kwargs["config"]["configurable"]
    assert configurable["review_check_run_id"] == 77
    assert kwargs["config"]["metadata"]["review_check_run_id"] == 77
    assert configurable["re_review"] is True
    assert configurable["last_reviewed_sha"] == "oldsha"
    assert configurable["head_sha"] == "headsha"
    assert "marked ready for review" in kwargs["input"]["messages"][0]["content"]
    head_sha_writes = [
        c.kwargs.get("head_sha")
        for c in set_metadata.await_args_list
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
        webhook_common,
        "get_profile",
        AsyncMock(return_value={"login": "alice", "review_draft_prs": False}),
    )
    monkeypatch.setattr(
        webhook_common, "get_team_settings", AsyncMock(return_value={"review_draft_prs": True})
    )

    await github_webhooks.process_github_pr_ready(_pr_payload(action="opened", draft=True))

    fake_client.runs.create.assert_not_called()


@pytest.mark.asyncio
async def test_pr_ready_draft_user_override_on_wins_over_team_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    _patch_dispatch_deps(monkeypatch, fake_client)
    monkeypatch.setattr(
        webhook_common,
        "get_profile",
        AsyncMock(return_value={"login": "alice", "review_draft_prs": True}),
    )
    monkeypatch.setattr(
        webhook_common,
        "get_team_settings",
        AsyncMock(return_value={"review_draft_prs": False}),
    )

    await github_webhooks.process_github_pr_ready(_pr_payload(action="opened", draft=True))

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
        webhook_common,
        "get_profile",
        AsyncMock(return_value={"login": "alice", "review_draft_prs": None}),
    )
    monkeypatch.setattr(
        webhook_common, "get_team_settings", AsyncMock(return_value={"review_draft_prs": True})
    )

    await github_webhooks.process_github_pr_ready(_pr_payload(action="opened", draft=True))

    fake_client.runs.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_pr_ready_draft_no_profile_falls_back_to_team_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    _patch_dispatch_deps(monkeypatch, fake_client)
    # External contributor — inherit team default (off).
    monkeypatch.setattr(webhook_common, "get_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(
        webhook_common,
        "get_team_settings",
        AsyncMock(return_value={"review_draft_prs": False}),
    )

    await github_webhooks.process_github_pr_ready(_pr_payload(action="opened", draft=True))

    fake_client.runs.create.assert_not_called()


@pytest.mark.asyncio
async def test_pr_ready_draft_no_profile_falls_back_to_team_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock()
    _patch_dispatch_deps(monkeypatch, fake_client)
    monkeypatch.setattr(webhook_common, "get_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(
        webhook_common, "get_team_settings", AsyncMock(return_value={"review_draft_prs": True})
    )

    await github_webhooks.process_github_pr_ready(_pr_payload(action="opened", draft=True))

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
            "agent.webhooks.common._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={"kind": "reviewer", "watch": True},
        ),
        patch(
            "agent.webhooks.common.get_profile",
            new_callable=AsyncMock,
            return_value={"login": "alice", "review_draft_prs": False},
        ),
        patch(
            "agent.webhooks.common.get_team_settings",
            new_callable=AsyncMock,
            return_value={"review_draft_prs": False},
        ),
        patch("agent.webhooks.common.set_reviewer_thread_metadata", side_effect=fake_set),
    ):
        await github_webhooks.process_github_pr_close(_converted_to_draft_payload())
    assert captured and captured[0][1]["watch"] is False


@pytest.mark.asyncio
async def test_converted_to_draft_keeps_watch_when_author_drafts_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_set = AsyncMock()
    with (
        patch(
            "agent.webhooks.common._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={"kind": "reviewer", "watch": True},
        ),
        patch(
            "agent.webhooks.common.get_profile",
            new_callable=AsyncMock,
            return_value={"login": "alice", "review_draft_prs": True},
        ),
        patch(
            "agent.webhooks.common.get_team_settings",
            new_callable=AsyncMock,
            return_value={"review_draft_prs": False},
        ),
        patch("agent.webhooks.common.set_reviewer_thread_metadata", new=fake_set),
    ):
        await github_webhooks.process_github_pr_close(_converted_to_draft_payload())
    fake_set.assert_not_called()


@pytest.mark.asyncio
async def test_converted_to_draft_keeps_watch_when_team_default_drafts_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_set = AsyncMock()
    with (
        patch(
            "agent.webhooks.common._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={"kind": "reviewer", "watch": True},
        ),
        # Author inherits team default — team has drafts on.
        patch(
            "agent.webhooks.common.get_profile",
            new_callable=AsyncMock,
            return_value={"login": "alice", "review_draft_prs": None},
        ),
        patch(
            "agent.webhooks.common.get_team_settings",
            new_callable=AsyncMock,
            return_value={"review_draft_prs": True},
        ),
        patch("agent.webhooks.common.set_reviewer_thread_metadata", new=fake_set),
    ):
        await github_webhooks.process_github_pr_close(_converted_to_draft_payload())
    fake_set.assert_not_called()
