"""Unit tests for the watch-mode webhook handlers."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from agent.webhooks import common as webhook_common
from agent.webhooks import github as github_webhooks


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
async def test_push_event_skips_branch_deletion(caplog: pytest.LogCaptureFixture) -> None:
    payload = _push_payload(
        ref="refs/heads/feat-x", after="0000000000000000000000000000000000000000"
    )
    with caplog.at_level(logging.INFO):
        await github_webhooks.process_github_push_event(payload)

    assert "repo=lc/repo ref=refs/heads/feat-x reason=branch deletion or missing SHA" in caplog.text


@pytest.mark.asyncio
async def test_push_event_logs_when_no_open_pr_is_found(
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = _push_payload(ref="refs/heads/feat-x", after="newsha")

    with (
        patch(
            "agent.webhooks.common._is_repo_auto_review_enabled",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "agent.webhooks.common.get_github_app_installation_token_with_expiry",
            new_callable=AsyncMock,
            return_value=("t", None),
        ),
        patch(
            "agent.webhooks.common._fetch_open_pr_for_branch",
            new_callable=AsyncMock,
            return_value=None,
        ),
        caplog.at_level(logging.INFO),
    ):
        await github_webhooks.process_github_push_event(payload)

    assert "repo=lc/repo ref=refs/heads/feat-x reason=no open PR found for branch" in caplog.text


@pytest.mark.asyncio
async def test_push_event_skips_when_thread_not_watching(
    caplog: pytest.LogCaptureFixture,
) -> None:
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
    create_check = AsyncMock()

    with (
        patch(
            "agent.webhooks.common._is_repo_auto_review_enabled",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "agent.webhooks.common.get_github_app_installation_token_with_expiry",
            new_callable=AsyncMock,
            return_value=("t", None),
        ),
        patch(
            "agent.webhooks.common._fetch_open_pr_for_branch",
            new_callable=AsyncMock,
            return_value=pr,
        ),
        patch(
            "agent.webhooks.common._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={"kind": "reviewer", "watch": False},
        ),
        patch(
            "agent.webhooks.common.create_review_check_run",
            new=create_check,
        ),
        patch("agent.webhooks.common.get_client", return_value=fake_client),
        caplog.at_level(logging.INFO),
    ):
        await github_webhooks.process_github_push_event(payload)
    fake_client.runs.create.assert_not_called()
    create_check.assert_not_awaited()
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata_result", "expected_reason"),
    [
        (RuntimeError("metadata unavailable"), "could not read reviewer thread metadata"),
        ({"kind": "reviewer"}, "reviewer thread watch metadata is invalid"),
        ({"kind": "unexpected", "watch": True}, "reviewer thread kind metadata is invalid"),
    ],
)
async def test_push_event_warns_when_reviewer_metadata_is_anomalous(
    metadata_result: dict[str, Any] | Exception,
    expected_reason: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = _push_payload(ref="refs/heads/feat-x", after="newsha")
    pr = {
        "number": 7,
        "html_url": "https://github.com/lc/repo/pull/7",
        "title": "T",
        "head": {"sha": "newsha", "ref": "feat-x"},
        "base": {"sha": "basesha", "ref": "main"},
    }
    get_metadata = AsyncMock()
    if isinstance(metadata_result, Exception):
        get_metadata.side_effect = metadata_result
    else:
        get_metadata.return_value = metadata_result
    create_check = AsyncMock()

    with (
        patch(
            "agent.webhooks.common._is_repo_auto_review_enabled",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "agent.webhooks.common.get_github_app_installation_token_with_expiry",
            new_callable=AsyncMock,
            return_value=("t", None),
        ),
        patch(
            "agent.webhooks.common._fetch_open_pr_for_branch",
            new_callable=AsyncMock,
            return_value=pr,
        ),
        patch("agent.webhooks.common._get_thread_metadata_safe", new=get_metadata),
        patch("agent.webhooks.common.create_review_check_run", new=create_check),
        caplog.at_level(logging.WARNING),
    ):
        await github_webhooks.process_github_push_event(payload)

    create_check.assert_not_awaited()
    assert f"lc/repo#7 head=newsha dropped: {expected_reason}" in caplog.text


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
            "agent.webhooks.common._is_repo_auto_review_enabled",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "agent.webhooks.common.get_github_app_installation_token_with_expiry",
            new_callable=AsyncMock,
            return_value=("t", None),
        ),
        patch(
            "agent.webhooks.common._fetch_open_pr_for_branch",
            new_callable=AsyncMock,
            return_value=pr,
        ),
        patch(
            "agent.webhooks.common._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={
                "kind": "reviewer",
                "watch": True,
                "last_reviewed_sha": "oldsha",
            },
        ),
        patch(
            "agent.webhooks.common._fetch_compare_diff",
            new_callable=AsyncMock,
            side_effect=["same diff", "same diff"],
        ),
        patch("agent.webhooks.common.set_reviewer_thread_metadata", new=set_metadata),
        patch(
            "agent.webhooks.common.create_review_check_run",
            new_callable=AsyncMock,
            return_value=42,
        ) as create_check,
        patch(
            "agent.webhooks.common.complete_review_check_run",
            new_callable=AsyncMock,
            return_value=True,
        ) as complete_check,
        patch("agent.webhooks.common.get_client", return_value=fake_client),
    ):
        await github_webhooks.process_github_push_event(payload)

    fake_client.runs.create.assert_not_called()
    set_metadata.assert_awaited_once()
    assert set_metadata.await_args is not None
    assert set_metadata.await_args.kwargs["last_reviewed_sha"] == "newsha"
    # Even without a re-review, a settled check lands on the new head so the
    # review stays visible after the head moves.
    create_check.assert_awaited_once()
    assert create_check.await_args is not None
    assert create_check.await_args.kwargs["head_sha"] == "newsha"
    complete_check.assert_awaited_once()
    assert complete_check.await_args is not None
    assert complete_check.await_args.kwargs["check_run_id"] == 42
    assert complete_check.await_args.kwargs["conclusion"] == "success"


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
            "agent.webhooks.common._is_repo_auto_review_enabled",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "agent.webhooks.common.get_github_app_installation_token",
            new_callable=AsyncMock,
            return_value="t",
        ),
        patch(
            "agent.webhooks.common.get_github_app_installation_token_with_expiry",
            new_callable=AsyncMock,
            return_value=("t", None),
        ),
        patch(
            "agent.webhooks.common._fetch_open_pr_for_branch",
            new_callable=AsyncMock,
            return_value=pr,
        ),
        patch(
            "agent.webhooks.common._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={
                "kind": "reviewer",
                "watch": True,
                "last_reviewed_sha": "oldsha",
            },
        ),
        patch(
            "agent.webhooks.common._fetch_compare_diff",
            new_callable=AsyncMock,
            side_effect=["old diff", "new diff"],
        ),
        patch(
            "agent.webhooks.common._ensure_thread_exists_for_metadata",
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
            "agent.webhooks.common.reviewer_assistant_for_dispatch",
            new_callable=AsyncMock,
            return_value="reviewer",
        ) as selector,
        patch("agent.webhooks.common.get_client", return_value=fake_client),
    ):
        await github_webhooks.process_github_push_event(payload)

    fake_client.runs.create.assert_awaited_once()
    assert fake_client.runs.create.await_args is not None
    args, kwargs = fake_client.runs.create.await_args
    assert args[1] == "reviewer"
    configurable = kwargs["config"]["configurable"]
    assert configurable["review_check_run_id"] == 99
    assert kwargs["config"]["metadata"]["review_check_run_id"] == 99
    assert configurable["re_review"] is True
    assert configurable["last_reviewed_sha"] == "oldsha"
    assert configurable["head_sha"] == "newsha"
    assert configurable["reviewer_event"] == ""
    selector.assert_awaited_once_with(
        re_review=True,
        finding_reply=False,
        explicit_request=False,
    )
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
    assert create_check.await_args is not None
    assert create_check.await_args.kwargs["head_sha"] == "newsha"
    check_id_writes = [
        c.kwargs.get("extra", {}).get("review_check_run_id")
        for c in set_meta.await_args_list
        if "review_check_run_id" in (c.kwargs.get("extra") or {})
    ]
    assert 99 in check_id_writes


@pytest.mark.asyncio
async def test_finding_reply_then_push_full_review_advances_head_and_settles_check() -> None:
    from agent.tools.publish_review import _publish_review_async

    checkpointed_config: dict[str, Any] = {"reviewer_event": "finding_reply"}
    shared_metadata: dict[str, Any] = {
        "kind": "reviewer",
        "watch": True,
        "last_reviewed_sha": "oldsha",
        "head_sha": "newsha",
    }

    async def update_metadata(thread_id: str, **kwargs: Any) -> None:
        del thread_id
        shared_metadata.update({key: value for key, value in kwargs.items() if value is not None})

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=[])),
        patch(
            "agent.tools.publish_review.resolve_review_head_sha",
            AsyncMock(return_value="newsha"),
        ),
        patch(
            "agent.tools.publish_review._open_swe_already_reviewed", AsyncMock(return_value=True)
        ),
        patch("agent.tools.publish_review.post_pull_request_review", AsyncMock()),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            AsyncMock(return_value=1),
        ),
        patch(
            "agent.tools.publish_review.set_reviewer_thread_metadata",
            AsyncMock(side_effect=update_metadata),
        ),
        patch("agent.tools.publish_review.clear_review_started_comment", AsyncMock()),
        patch("agent.tools.publish_review._settle_or_defer_review_check", AsyncMock()) as settle,
    ):
        result = await _publish_review_async(
            owner="lc",
            repo="repo",
            pr_number=7,
            head_sha="oldsha",
            token="t",
            severity_threshold="medium",
            cap=15,
            is_re_review=True,
            is_finding_reply=True,
        )

    assert result["skipped_empty_re_review"] is True
    assert shared_metadata["last_reviewed_sha"] == "oldsha"
    settle.assert_not_awaited()

    payload = _push_payload(ref="refs/heads/feat-x", after="newsha")
    pr = {
        "number": 7,
        "html_url": "https://github.com/lc/repo/pull/7",
        "title": "T",
        "head": {"sha": "newsha", "ref": "feat-x"},
        "base": {"sha": "basesha", "ref": "main"},
    }

    async def create_run(*args: Any, **kwargs: Any) -> dict[str, str]:
        checkpointed_config.update(kwargs["config"]["configurable"])
        return {"run_id": "run-2"}

    fake_client = MagicMock()
    fake_client.runs.create = AsyncMock(side_effect=create_run)

    with (
        patch(
            "agent.webhooks.common._is_repo_auto_review_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "agent.webhooks.common.get_github_app_installation_token_with_expiry",
            AsyncMock(return_value=("t", None)),
        ),
        patch(
            "agent.webhooks.common._fetch_open_pr_for_branch",
            AsyncMock(return_value=pr),
        ),
        patch(
            "agent.webhooks.common._get_thread_metadata_safe",
            AsyncMock(return_value=shared_metadata),
        ),
        patch(
            "agent.webhooks.common._fetch_compare_diff",
            AsyncMock(side_effect=["old diff", "new diff"]),
        ),
        patch(
            "agent.webhooks.common._ensure_thread_exists_for_metadata",
            AsyncMock(return_value=True),
        ),
        patch("agent.webhooks.common.set_reviewer_thread_metadata", AsyncMock()),
        patch("agent.webhooks.common.create_review_check_run", AsyncMock(return_value=99)),
        patch(
            "agent.webhooks.common.reviewer_assistant_for_dispatch",
            AsyncMock(return_value="reviewer"),
        ),
        patch("agent.webhooks.common.get_client", return_value=fake_client),
    ):
        await github_webhooks.process_github_push_event(payload)

    fake_client.runs.create.assert_awaited_once()
    assert fake_client.runs.create.await_args is not None
    _, kwargs = fake_client.runs.create.await_args
    configurable = kwargs["config"]["configurable"]
    assert configurable["last_reviewed_sha"] == "oldsha"
    assert configurable["head_sha"] == "newsha"
    assert configurable["review_check_run_id"] == 99
    assert kwargs["config"]["metadata"]["review_check_run_id"] == 99
    assert checkpointed_config["reviewer_event"] == ""

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch(
            "agent.tools.publish_review.get_thread_metadata",
            AsyncMock(
                return_value={
                    **shared_metadata,
                    "head_sha": "newsha",
                    "review_check_run_id": 99,
                    "current_reviewer_run_id": "run-2",
                }
            ),
        ),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=[])),
        patch(
            "agent.tools.publish_review.resolve_review_head_sha",
            AsyncMock(return_value="newsha"),
        ),
        patch(
            "agent.tools.publish_review._open_swe_already_reviewed", AsyncMock(return_value=True)
        ),
        patch("agent.tools.publish_review.post_pull_request_review", AsyncMock()),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            AsyncMock(return_value=1),
        ),
        patch(
            "agent.tools.publish_review.set_reviewer_thread_metadata",
            AsyncMock(side_effect=update_metadata),
        ),
        patch("agent.tools.publish_review.clear_review_started_comment", AsyncMock()),
        patch("agent.tools.publish_review._settle_or_defer_review_check", AsyncMock()) as settle,
    ):
        result = await _publish_review_async(
            owner="lc",
            repo="repo",
            pr_number=7,
            head_sha=checkpointed_config["head_sha"],
            token="t",
            severity_threshold="medium",
            cap=15,
            is_re_review=True,
            is_finding_reply=checkpointed_config["reviewer_event"] == "finding_reply",
            review_check_run_id=checkpointed_config["review_check_run_id"],
            langgraph_run_id="run-2",
        )

    assert result["skipped_empty_re_review"] is True
    assert shared_metadata["last_reviewed_sha"] == "newsha"
    settle.assert_awaited_once()
    assert settle.await_args is not None
    assert settle.await_args.kwargs["review_check_run_id"] == 99


@pytest.mark.asyncio
async def test_push_event_uses_payload_head_when_pr_fetch_is_stale() -> None:
    payload = _push_payload(ref="refs/heads/feat-x", after="newsha")
    pr = {
        "number": 7,
        "html_url": "https://github.com/lc/repo/pull/7",
        "title": "T",
        "head": {"sha": "stale-sha", "ref": "feat-x"},
        "base": {"sha": "basesha", "ref": "main"},
    }
    fake_client = MagicMock()
    dispatch_run = AsyncMock(return_value={"run_id": "run-2"})

    with (
        patch("agent.webhooks.common._is_repo_auto_review_enabled", AsyncMock(return_value=True)),
        patch(
            "agent.webhooks.common.get_github_app_installation_token_with_expiry",
            AsyncMock(return_value=("t", None)),
        ),
        patch("agent.webhooks.common._fetch_open_pr_for_branch", AsyncMock(return_value=pr)),
        patch(
            "agent.webhooks.common._get_thread_metadata_safe",
            AsyncMock(
                return_value={
                    "kind": "reviewer",
                    "watch": True,
                    "last_reviewed_sha": "oldsha",
                }
            ),
        ),
        patch(
            "agent.webhooks.common._is_pr_diff_unchanged_since_last_review",
            AsyncMock(return_value=False),
        ),
        patch(
            "agent.webhooks.common._ensure_thread_exists_for_metadata",
            AsyncMock(return_value=True),
        ),
        patch("agent.webhooks.common.fetch_pr_review_threads", AsyncMock(return_value=[])),
        patch("agent.webhooks.common.reconcile_findings_with_review_threads", AsyncMock()),
        patch("agent.webhooks.common.set_reviewer_thread_metadata", AsyncMock()) as set_metadata,
        patch(
            "agent.webhooks.common.create_review_check_run", AsyncMock(return_value=99)
        ) as create_check,
        patch(
            "agent.webhooks.common.reviewer_assistant_for_dispatch",
            AsyncMock(return_value="reviewer"),
        ),
        patch("agent.webhooks.common.dispatch_agent_run", dispatch_run),
        patch("agent.webhooks.common._store_current_reviewer_run_id", AsyncMock()),
        patch("agent.webhooks.common.get_client", return_value=fake_client),
    ):
        await github_webhooks.process_github_push_event(payload)

    create_check.assert_awaited_once()
    assert create_check.await_args is not None
    assert create_check.await_args.kwargs["head_sha"] == "newsha"
    dispatch_run.assert_awaited_once()
    assert dispatch_run.await_args is not None
    configurable = dispatch_run.await_args.args[2]
    assert configurable["head_sha"] == "newsha"
    head_sha_writes = [
        item.kwargs.get("head_sha")
        for item in set_metadata.await_args_list
        if item.kwargs.get("head_sha") is not None
    ]
    assert head_sha_writes == ["newsha"]


@pytest.mark.asyncio
async def test_push_event_new_payload_head_is_not_deduped_by_stale_pr_head() -> None:
    payload = _push_payload(ref="refs/heads/feat-x", after="newsha")
    pr = {
        "number": 7,
        "html_url": "https://github.com/lc/repo/pull/7",
        "title": "T",
        "head": {"sha": "oldsha", "ref": "feat-x"},
        "base": {"sha": "basesha", "ref": "main"},
    }
    diff_unchanged = AsyncMock(return_value=True)

    with (
        patch("agent.webhooks.common._is_repo_auto_review_enabled", AsyncMock(return_value=True)),
        patch(
            "agent.webhooks.common.get_github_app_installation_token_with_expiry",
            AsyncMock(return_value=("t", None)),
        ),
        patch("agent.webhooks.common._fetch_open_pr_for_branch", AsyncMock(return_value=pr)),
        patch(
            "agent.webhooks.common._get_thread_metadata_safe",
            AsyncMock(
                return_value={
                    "kind": "reviewer",
                    "watch": True,
                    "last_reviewed_sha": "oldsha",
                }
            ),
        ),
        patch(
            "agent.webhooks.common._is_pr_diff_unchanged_since_last_review",
            diff_unchanged,
        ),
        patch("agent.webhooks.common.set_reviewer_thread_metadata", AsyncMock()) as set_metadata,
        patch(
            "agent.webhooks.common.create_review_check_run", AsyncMock(return_value=42)
        ) as create_check,
        patch("agent.webhooks.common.complete_review_check_run", AsyncMock()),
    ):
        await github_webhooks.process_github_push_event(payload)

    diff_unchanged.assert_awaited_once()
    assert diff_unchanged.await_args is not None
    assert diff_unchanged.await_args.kwargs["head_sha"] == "newsha"
    set_metadata.assert_awaited_once_with(
        webhook_common.generate_reviewer_thread_id("lc", "repo", 7),
        last_reviewed_sha="newsha",
    )
    create_check.assert_awaited_once()
    assert create_check.await_args is not None
    assert create_check.await_args.kwargs["head_sha"] == "newsha"


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
            "agent.webhooks.common._is_repo_auto_review_enabled",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "agent.webhooks.common.get_github_app_installation_token",
            new_callable=AsyncMock,
            return_value="t",
        ),
        patch(
            "agent.webhooks.common._fetch_open_pr_for_branch",
            new_callable=AsyncMock,
            return_value=pr,
        ),
        patch(
            "agent.webhooks.common._get_thread_metadata_safe",
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
async def test_reviewer_token_for_repo_public_scopes_by_id() -> None:
    get_token = AsyncMock(return_value=("scoped", "exp"))
    with patch("agent.webhooks.common.get_github_app_installation_token_with_expiry", get_token):
        token, expires = await webhook_common._reviewer_token_for_repo(
            {"owner": "lc", "name": "repo"}, repo_private=False, repo_id=123
        )
    assert (token, expires) == ("scoped", "exp")
    get_token.assert_awaited_once_with(target_repo="lc/repo", repository_ids=[123])


@pytest.mark.asyncio
async def test_reviewer_token_for_repo_public_scopes_by_name_without_id() -> None:
    get_token = AsyncMock(return_value=("scoped", "exp"))
    with patch("agent.webhooks.common.get_github_app_installation_token_with_expiry", get_token):
        await webhook_common._reviewer_token_for_repo(
            {"owner": "lc", "name": "repo"}, repo_private=False, repo_id=None
        )
    get_token.assert_awaited_once_with(target_repo="lc/repo", repositories=["repo"])


@pytest.mark.asyncio
async def test_reviewer_token_for_repo_private_scopes_by_id() -> None:
    get_token = AsyncMock(return_value=("full", "exp"))
    with patch("agent.webhooks.common.get_github_app_installation_token_with_expiry", get_token):
        await webhook_common._reviewer_token_for_repo(
            {"owner": "lc", "name": "repo"}, repo_private=True, repo_id=123
        )
    get_token.assert_awaited_once_with(target_repo="lc/repo", repository_ids=[123])


@pytest.mark.asyncio
async def test_reviewer_token_for_repo_unknown_privacy_scopes_by_id() -> None:
    get_token = AsyncMock(return_value=("full", "exp"))
    with patch("agent.webhooks.common.get_github_app_installation_token_with_expiry", get_token):
        await webhook_common._reviewer_token_for_repo(
            {"owner": "lc", "name": "repo"}, repo_private=None, repo_id=123
        )
    get_token.assert_awaited_once_with(target_repo="lc/repo", repository_ids=[123])


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
            "agent.webhooks.common._is_repo_auto_review_enabled",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("agent.webhooks.common.get_github_app_installation_token_with_expiry", get_token),
        patch(
            "agent.webhooks.common._fetch_open_pr_for_branch",
            new_callable=AsyncMock,
            return_value=pr,
        ),
        patch(
            "agent.webhooks.common._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={"kind": "reviewer", "watch": True},
        ),
        patch(
            "agent.webhooks.common._ensure_thread_exists_for_metadata",
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

    get_token.assert_awaited_once_with(target_repo="lc/repo", repository_ids=[123])
    assert fake_client.runs.create.await_args is not None
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
            "agent.webhooks.common._is_repo_auto_review_enabled",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("agent.webhooks.common.get_github_app_installation_token_with_expiry", get_token),
        patch(
            "agent.webhooks.common._fetch_open_pr_for_branch",
            new_callable=AsyncMock,
            return_value=pr,
        ),
        patch(
            "agent.webhooks.common._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={"kind": "reviewer", "watch": True},
        ),
        patch(
            "agent.webhooks.common._ensure_thread_exists_for_metadata",
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

    assert get_token.await_args_list == [
        call(target_repo="lc/repo", repositories=["repo"]),
        call(target_repo="lc/repo", repository_ids=[456]),
    ]
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
            "agent.webhooks.common._is_repo_auto_review_enabled",
            new_callable=AsyncMock,
            return_value=False,
        ) as auto_review_enabled,
        patch(
            "agent.webhooks.common._get_thread_metadata_safe",
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
            "agent.webhooks.common._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={"kind": "reviewer", "watch": False},
        ),
        patch("agent.webhooks.common.set_reviewer_thread_metadata", side_effect=fake_set),
    ):
        await github_webhooks.process_github_pr_close(_pr_close_payload(action="reopened"))
    assert captured and captured[0][1]["watch"] is True


@pytest.mark.asyncio
async def test_pr_close_skips_non_reviewer_threads() -> None:
    fake_set = AsyncMock()
    with (
        patch(
            "agent.webhooks.common._get_thread_metadata_safe",
            new_callable=AsyncMock,
            return_value={"kind": "agent"},
        ),
        patch("agent.webhooks.common.set_reviewer_thread_metadata", new=fake_set),
    ):
        await github_webhooks.process_github_pr_close(_pr_close_payload(action="closed"))
    fake_set.assert_not_called()
