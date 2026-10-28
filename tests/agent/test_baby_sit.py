import asyncio
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid7

import pytest

from openswe import baby_sit
from openswe.github import app as github_app
from openswe.github.ci import RequiredCheck
from openswe.github.http import RepoClient
from openswe.github.pull_request_status import PullRequestClient
from openswe.human_review.requests import HumanReviewRequest
from openswe.slack.client import GitHubPrRef
from openswe.source_context import SourceContext
from openswe.tasks.store import Task


async def _start_watch() -> baby_sit.BabySitWatch:
    return await baby_sit.start_watch(
        pr_ref=GitHubPrRef("Acme", "Repo", 7, "https://github.com/Acme/Repo/pull/7"),
        title="Fix the build",
        head_sha="head-1",
        head_ref="feature",
        installation_id=42,
        thread_id="thread-1",
        run_config={
            "thread_id": "thread-1",
            "source": "slack",
            "slack_thread": {"channel_id": "C1", "thread_ts": "1.2"},
        },
        source_context=SourceContext.parse(
            {"slack_thread": {"channel_id": "C1", "thread_ts": "1.2"}}
        ),
    )


async def test_failure_dispatch_is_deduplicated_until_retry_is_recorded(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _start_watch()
    monkeypatch.setattr(
        github_app, "get_github_app_installation_token", AsyncMock(return_value="t")
    )
    monkeypatch.setattr(
        PullRequestClient,
        "pull",
        AsyncMock(return_value={"state": "open", "head": {"sha": "head-1"}}),
    )
    checks = [
        {
            "id": 11,
            "name": "tests",
            "status": "completed",
            "conclusion": "failure",
            "details_url": "https://github.com/Acme/Repo/actions/runs/1",
            "completed_at": "2026-01-01T00:00:00Z",
        }
    ]
    list_checks = AsyncMock(return_value=checks)
    monkeypatch.setattr(RepoClient, "check_runs", list_checks)
    monkeypatch.setattr(RepoClient, "commit_statuses", AsyncMock(return_value=[]))
    dispatch = AsyncMock(return_value={"run_id": "run-1"})
    monkeypatch.setattr(baby_sit, "dispatch_agent_run", dispatch)

    assert await baby_sit.evaluate_watch("acme/repo#7") == "dispatched"
    assert dispatch.await_args is not None
    assert dispatch.await_args.kwargs["multitask_strategy"] == "enqueue"
    assert await baby_sit.evaluate_watch("acme/repo#7") == "duplicate"
    assert dispatch.await_count == 1

    monkeypatch.setattr(baby_sit, "post_slack_thread_reply", AsyncMock(return_value=True))
    await baby_sit.record_retry(
        "acme/repo#7",
        thread_id="thread-1",
        head_sha="head-1",
        check_name="tests",
        evidence="runner timeout",
    )
    checks[0] = {**checks[0], "id": 12, "completed_at": "2026-01-01T00:10:00Z"}
    assert await baby_sit.evaluate_watch("acme/repo#7") == "dispatched"
    assert dispatch.await_count == 2


async def test_concurrent_failure_evaluations_dispatch_once(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _start_watch()
    monkeypatch.setattr(
        github_app, "get_github_app_installation_token", AsyncMock(return_value="t")
    )
    monkeypatch.setattr(
        PullRequestClient,
        "pull",
        AsyncMock(return_value={"state": "open", "head": {"sha": "head-1"}}),
    )
    monkeypatch.setattr(
        RepoClient,
        "check_runs",
        AsyncMock(
            return_value=[
                {"id": 11, "name": "tests", "status": "completed", "conclusion": "failure"}
            ]
        ),
    )
    monkeypatch.setattr(RepoClient, "commit_statuses", AsyncMock(return_value=[]))
    dispatch_started = asyncio.Event()
    release_dispatch = asyncio.Event()

    async def dispatch_run(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        dispatch_started.set()
        await release_dispatch.wait()
        return {"run_id": "run-1"}

    dispatch = AsyncMock(side_effect=dispatch_run)
    monkeypatch.setattr(baby_sit, "dispatch_agent_run", dispatch)

    first = asyncio.create_task(baby_sit.evaluate_watch("acme/repo#7"))
    await dispatch_started.wait()
    assert await baby_sit.evaluate_watch("acme/repo#7") == "busy"
    release_dispatch.set()

    assert await first == "dispatched"
    dispatch.assert_awaited_once()
    assert await baby_sit.evaluate_watch("acme/repo#7") == "duplicate"


@pytest.mark.parametrize("card_state", ["none", "ready", "draft", "other_thread"])
async def test_green_webhook_keeps_watching_without_waking_for_draft_card(
    registry_db: None, monkeypatch: pytest.MonkeyPatch, card_state: str
) -> None:
    await _start_watch()
    approval = (
        HumanReviewRequest(
            pull_request_id=uuid7(),
            head_sha="head-1",
            kind="expedited",
            thread_id="other-thread" if card_state == "other_thread" else "thread-1",
            awaiting_ready=card_state in {"draft", "other_thread"},
        )
        if card_state != "none"
        else None
    )
    monkeypatch.setattr(HumanReviewRequest, "active_for", AsyncMock(return_value=approval))
    monkeypatch.setattr(
        github_app, "get_github_app_installation_token", AsyncMock(return_value="t")
    )
    monkeypatch.setattr(
        PullRequestClient,
        "pull",
        AsyncMock(
            return_value={"state": "open", "head": {"sha": "head-1"}, "base": {"ref": "main"}}
        ),
    )
    required = AsyncMock(return_value={RequiredCheck("tests"), RequiredCheck("e2e")})
    monkeypatch.setattr(RequiredCheck, "for_branch", required)
    monkeypatch.setattr(
        RepoClient,
        "check_runs",
        AsyncMock(
            return_value=[
                {"id": 1, "name": "tests", "status": "completed", "conclusion": "success"}
            ]
        ),
    )
    monkeypatch.setattr(RepoClient, "commit_statuses", AsyncMock(return_value=[]))
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(baby_sit, "post_slack_thread_reply", notify)
    dispatch = AsyncMock(return_value={"run_id": "run-1"})
    monkeypatch.setattr(baby_sit, "dispatch_agent_run", dispatch)
    payload = {
        "installation": {"id": 99},
        "repository": {"owner": {"login": "acme"}, "name": "repo"},
        "check_run": {"status": "completed", "conclusion": "success", "head_sha": "head-1"},
    }

    assert await baby_sit.evaluate_watch("acme/repo#7") == "pending"
    dispatch.assert_not_awaited()

    required.return_value = {RequiredCheck("tests")}
    assert await baby_sit.handle_ci_webhook(payload, "check_run") == {
        "matched": 1,
        "dispatched": 0,
    }
    if card_state == "draft":
        dispatch.assert_not_awaited()
    else:
        dispatch.assert_awaited_once()
        assert dispatch.await_args is not None
        expected = "merge_expedited_pr" if card_state == "ready" else "checks are green"
        assert expected in dispatch.await_args.args[1]
    notify.assert_not_awaited()
    assert len(await baby_sit.WATCHES.list_active()) == 1
    dispatch.reset_mock()
    assert await baby_sit.evaluate_watch("acme/repo#7") == "watching"
    dispatch.assert_not_awaited()

    if card_state == "draft":
        assert approval is not None
        approval.awaiting_ready = False
        assert await baby_sit.evaluate_watch("acme/repo#7") == "watching"
        dispatch.assert_awaited_once()
        dispatch.reset_mock()

    monkeypatch.setattr(
        PullRequestClient,
        "pull",
        AsyncMock(
            return_value={
                "state": "open",
                "head": {"sha": "head-2", "ref": "feature"},
                "base": {"ref": "main"},
            }
        ),
    )
    monkeypatch.setattr(
        RepoClient,
        "check_runs",
        AsyncMock(
            return_value=[
                {"id": 2, "name": "tests", "status": "completed", "conclusion": "failure"}
            ]
        ),
    )
    payload["check_run"]["head_sha"] = "head-2"
    payload["check_run"]["conclusion"] = "failure"
    payload["check_run"]["check_suite"] = {"head_branch": "feature"}
    assert await baby_sit.handle_ci_webhook(payload, "check_run") == {
        "matched": 1,
        "dispatched": 1,
    }
    dispatch.assert_awaited_once()
    assert dispatch.await_args is not None
    assert "head-2" in dispatch.await_args.args[1]
    dispatch.reset_mock()
    monkeypatch.setattr(
        RepoClient,
        "check_runs",
        AsyncMock(
            return_value=[
                {"id": 3, "name": "tests", "status": "completed", "conclusion": "success"}
            ]
        ),
    )
    assert await baby_sit.evaluate_watch("acme/repo#7") == "watching"
    dispatch.assert_awaited_once()
    dispatch.reset_mock()
    assert await baby_sit.evaluate_watch("acme/repo#7") == "watching"
    dispatch.assert_not_awaited()

    monkeypatch.setattr(PullRequestClient, "pull", AsyncMock(return_value={"state": "closed"}))
    assert await baby_sit.evaluate_watch("acme/repo#7") == "stopped"
    assert await baby_sit.WATCHES.get("acme/repo#7") is None


@pytest.mark.parametrize("recovers", [False, True])
async def test_ready_wakeup_failures_are_bounded_and_reset_on_recovery(
    registry_db: None, monkeypatch: pytest.MonkeyPatch, recovers: bool
) -> None:
    await _start_watch()
    dispatch = AsyncMock(side_effect=RuntimeError("Agent service unavailable"))
    monkeypatch.setattr(baby_sit, "dispatch_agent_run", dispatch)
    notify = AsyncMock()
    monkeypatch.setattr(baby_sit, "post_slack_thread_reply", notify)

    for expected in range(1, baby_sit.MAX_EVALUATION_ERRORS):
        watch = await baby_sit.WATCHES.get("acme/repo#7")
        assert watch is not None
        assert await baby_sit._finish_ready(watch) == "error"
        saved = await baby_sit.WATCHES.get(watch.key)
        assert saved is not None
        assert saved.ready_dispatch_errors == expected
        assert saved.ready_head_sha == ""

    dispatch.side_effect = None
    watch = await baby_sit.WATCHES.get("acme/repo#7")
    assert watch is not None
    if recovers:
        assert await baby_sit._finish_ready(watch) == "watching"
        saved = await baby_sit.WATCHES.get(watch.key)
        assert saved is not None
        assert saved.ready_dispatch_errors == 0
        assert saved.ready_head_sha == "head-1"
    else:
        dispatch.side_effect = [RuntimeError("Agent service unavailable"), {"run_id": "notice"}]
        assert await baby_sit._finish_ready(watch) == "stopped"
        assert await baby_sit.WATCHES.get(watch.key) is None
        assert dispatch.await_args is not None
        message = dispatch.await_args.args[1]
        assert "/baby-sit --terminal" in message
        assert "could not resume its agent after 3 attempts" in message
        assert "restart the CI watch" in message
    notify.assert_not_awaited()


async def test_terminal_notification_falls_back_to_originating_agent_thread(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch = await _start_watch()
    watch.source_context = SourceContext()
    watch.run_config = {"thread_id": "thread-1", "source": "dashboard"}
    await baby_sit.WATCHES.save(watch)
    monkeypatch.setattr(
        github_app, "get_github_app_installation_token", AsyncMock(return_value="t")
    )
    monkeypatch.setattr(
        PullRequestClient,
        "pull",
        AsyncMock(return_value={"state": "open", "head": {"sha": "head-1"}}),
    )
    monkeypatch.setattr(
        RepoClient,
        "check_runs",
        AsyncMock(
            return_value=[
                {"id": 1, "name": "tests", "status": "completed", "conclusion": "cancelled"}
            ]
        ),
    )
    monkeypatch.setattr(RepoClient, "commit_statuses", AsyncMock(return_value=[]))
    dispatch = AsyncMock(return_value={"run_id": "run-1"})
    monkeypatch.setattr(baby_sit, "dispatch_agent_run", dispatch)

    assert await baby_sit.evaluate_watch("acme/repo#7") == "stopped"
    dispatch.assert_awaited_once()
    assert dispatch.await_args is not None
    assert dispatch.await_args.args[0] == "thread-1"
    assert "/baby-sit --terminal" in dispatch.await_args.args[1]


async def test_record_retry_caps_attempts_and_deduplicates_flake_alert(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _start_watch()
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(baby_sit, "post_slack_thread_reply", notify)

    for expected in range(1, 4):
        result = await baby_sit.record_retry(
            "acme/repo#7",
            thread_id="thread-1",
            head_sha="head-1",
            check_name="tests",
            evidence="runner timeout",
            details_url="https://github.com/Acme/Repo/actions/runs/1",
        )
        assert result["success"] is True
        assert result["retry_count"] == expected

    blocked = await baby_sit.record_retry(
        "acme/repo#7",
        thread_id="thread-1",
        head_sha="head-1",
        check_name="tests",
        evidence="runner timeout",
    )
    assert blocked == {"success": False, "error": "Flaky rerun limit reached"}
    assert notify.await_count == 1


async def test_new_head_resets_retry_budget(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _start_watch()
    monkeypatch.setattr(baby_sit, "post_slack_thread_reply", AsyncMock(return_value=True))
    for _ in range(3):
        await baby_sit.record_retry(
            "acme/repo#7",
            thread_id="thread-1",
            head_sha="head-1",
            check_name="tests",
            evidence="runner timeout",
        )
    monkeypatch.setattr(
        github_app, "get_github_app_installation_token", AsyncMock(return_value="t")
    )
    monkeypatch.setattr(
        PullRequestClient,
        "pull",
        AsyncMock(return_value={"state": "open", "head": {"sha": "head-2"}}),
    )
    monkeypatch.setattr(
        RepoClient,
        "check_runs",
        AsyncMock(return_value=[{"id": 2, "name": "tests", "status": "in_progress"}]),
    )
    monkeypatch.setattr(RepoClient, "commit_statuses", AsyncMock(return_value=[]))

    assert await baby_sit.evaluate_watch("acme/repo#7") == "pending"
    watch = await baby_sit.WATCHES.get("acme/repo#7")
    assert watch is not None
    assert watch.head_sha == "head-2"
    assert watch.retry_count == 0


async def test_only_the_watching_task_can_take_over_or_record_retries(
    registry_db: None,
) -> None:
    watch = await _start_watch()
    assert watch.thread_id == "thread-1"
    task = await Task.for_thread("thread-1", title="", workspace="default")
    assert watch.task_id == task.id

    with pytest.raises(ValueError, match="another task"):
        await baby_sit.start_watch(
            pr_ref=GitHubPrRef("Acme", "Repo", 7, "https://github.com/Acme/Repo/pull/7"),
            title="Fix the build",
            head_sha="head-1",
            head_ref="feature",
            installation_id=42,
            thread_id="thread-2",
            run_config={"thread_id": "thread-2"},
            source_context=SourceContext(),
        )
    assert await baby_sit.record_retry(
        "acme/repo#7",
        thread_id="thread-2",
        head_sha="head-1",
        check_name="tests",
        evidence="runner timeout",
    ) == {"success": False, "error": "This watch belongs to another task"}
