import asyncio
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid7

import httpx2
import pytest

from openswe import baby_sit
from openswe.github import app as github_app
from openswe.github.ci import CheckFailure, RequiredCheck
from openswe.github.http import GitHubError, RepoClient
from openswe.github.pull_request_status import PullRequestClient
from openswe.human_review.requests import HumanReviewRequest
from openswe.slack.client import GitHubPrRef
from openswe.source_context import SourceContext
from openswe.tasks.store import Task
from tests.support.github_sdk import mock_github_sdk


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


def _check_run(run_id: int, name: str, conclusion: str | None) -> dict[str, object]:
    return {
        "id": run_id,
        "name": name,
        "status": "completed" if conclusion else "in_progress",
        "conclusion": conclusion,
        "details_url": f"https://github.com/Acme/Repo/actions/runs/{run_id}",
    }


def _failure(name: str, run_id: int | None = None) -> CheckFailure:
    return CheckFailure(name=name, conclusion="failure", url="", run_id=run_id)


_A_RUN = _failure("a", 1)
_A_RERUN = _failure("a", 4)
_B_RUN = _failure("b", 2)
_C_RUN = _failure("c", 3)
_DEPLOY_STATUS = _failure("deploy")

_Step = tuple[str, list[CheckFailure], bool, list[str]]


@pytest.mark.parametrize(
    ("read_method", "status_code", "body", "retryable"),
    [
        ("token", 404, "Not Found", False),
        ("token", 403, "Installation suspended", False),
        ("token", 503, "Service unavailable", True),
        ("token", 403, "API rate limit exceeded", True),
        ("token", None, "Connection failed", True),
        ("pull", 403, "API rate limit exceeded for installation", True),
        ("pull", 503, "Service unavailable", True),
        ("pull", None, "Connection failed", True),
        ("check_runs", 503, "Service unavailable", True),
        ("commit_statuses", 403, "secondary rate limit", True),
        ("pull", 404, "Not Found", False),
        ("pull", 401, "Bad credentials", False),
        ("pull", 403, "Resource not accessible", False),
    ],
)
async def test_github_read_failures_only_end_watches_when_persistent(
    registry_db: None,
    monkeypatch: pytest.MonkeyPatch,
    read_method: str,
    status_code: int | None,
    body: str,
    retryable: bool,
) -> None:
    watch = await _start_watch()
    token = AsyncMock(return_value="t")
    if read_method != "token":
        monkeypatch.setattr(github_app, "get_github_app_installation_token", token)
    reads = {
        "pull": AsyncMock(return_value={"state": "open", "head": {"sha": "head-1"}}),
        "check_runs": AsyncMock(return_value=[{"name": "tests", "status": "in_progress"}]),
        "commit_statuses": AsyncMock(return_value=[]),
    }
    monkeypatch.setattr(PullRequestClient, "pull", reads["pull"])
    monkeypatch.setattr(RepoClient, "check_runs", reads["check_runs"])
    monkeypatch.setattr(RepoClient, "commit_statuses", reads["commit_statuses"])
    failure = (
        GitHubError(
            httpx2.Response(
                status_code,
                text=body,
                request=httpx2.Request("GET", "https://api.github.com/test"),
            )
        )
        if status_code is not None
        else httpx2.ConnectError(body)
    )
    read = reads.get(read_method, token)
    token_failed = True
    if read_method == "token":
        monkeypatch.setattr(github_app, "GITHUB_APP_ID", "1")
        monkeypatch.setattr(github_app, "GITHUB_APP_PRIVATE_KEY", "test-key")
        monkeypatch.setattr(github_app, "_TOKEN_CACHE", {})

        def handle(request: httpx2.Request) -> httpx2.Response:
            if token_failed:
                if status_code is None:
                    raise httpx2.ConnectError(body, request=request)
                return httpx2.Response(status_code, json={"message": body})
            return httpx2.Response(201, json={"token": "t", "expires_at": "2099-01-01T00:00:00Z"})

        mock_github_sdk(monkeypatch, handle)
    else:
        read.side_effect = failure
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(baby_sit, "_notify_watch", notify)
    dispatch = AsyncMock()
    monkeypatch.setattr(baby_sit, "dispatch_agent_run", dispatch)

    for attempt in range(1, 4):
        result = await baby_sit.evaluate_watch(watch.key)
        stored = await baby_sit.WATCHES.get(watch.key)
        if retryable or attempt < 3:
            assert result == "error"
            assert stored is not None and stored.active
            assert stored.evaluation_errors == (0 if retryable else attempt)
        else:
            assert result == "stopped"
            assert stored is None

    if retryable:
        notify.assert_not_awaited()
        dispatch.assert_not_awaited()
        read.side_effect = None
        token_failed = False
        assert await baby_sit.evaluate_watch(watch.key) == "pending"
        stored = await baby_sit.WATCHES.get(watch.key)
        assert stored is not None and stored.active
    else:
        # A Slack-started watch hands its terminal notice to the owning agent thread.
        assert dispatch.await_args is not None
        message = dispatch.await_args.args[1]
        assert (
            "GitHub App token unavailable"
            if read_method == "token"
            else "not found"
            if status_code == 404
            else "denied access"
        ) in message


async def test_a_dispatch_error_is_retried_with_only_unsent_failures(
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
    checks = [_check_run(1, "approval", "failure"), _check_run(2, "tests", None)]
    monkeypatch.setattr(RepoClient, "check_runs", AsyncMock(return_value=checks))
    monkeypatch.setattr(RepoClient, "commit_statuses", AsyncMock(return_value=[]))
    dispatch = AsyncMock(
        side_effect=[RuntimeError("Agent service unavailable"), *[{"run_id": "run-1"}] * 2]
    )
    monkeypatch.setattr(baby_sit, "dispatch_agent_run", dispatch)

    assert await baby_sit.evaluate_watch("acme/repo#7") == "error"
    assert await baby_sit.evaluate_watch("acme/repo#7") == "dispatched"
    assert dispatch.await_args is not None
    assert dispatch.await_args.kwargs["multitask_strategy"] == "enqueue"

    checks[1] = _check_run(2, "tests", "failure")
    assert await baby_sit.evaluate_watch("acme/repo#7") == "dispatched"
    assert "- tests (failure)" in dispatch.await_args.args[1]
    assert "- approval (failure)" not in dispatch.await_args.args[1]


async def test_spent_rerun_budget_stops_the_watch_only_on_a_new_failure(
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
    checks = [_check_run(1, "approval", "failure"), _check_run(2, "tests", None)]
    monkeypatch.setattr(RepoClient, "check_runs", AsyncMock(return_value=checks))
    monkeypatch.setattr(RepoClient, "commit_statuses", AsyncMock(return_value=[]))
    dispatch = AsyncMock(return_value={"run_id": "run-1"})
    monkeypatch.setattr(baby_sit, "dispatch_agent_run", dispatch)
    monkeypatch.setattr(baby_sit, "post_slack_thread_reply", AsyncMock(return_value=True))

    assert await baby_sit.evaluate_watch("acme/repo#7") == "dispatched"
    for _ in range(3):
        await baby_sit.record_retry(
            "acme/repo#7",
            thread_id="thread-1",
            head_sha="head-1",
            check_name="tests",
            evidence="runner timeout",
        )
    await _start_watch()
    checks[1] = _check_run(3, "tests", "success")
    assert await baby_sit.evaluate_watch("acme/repo#7") == "duplicate"
    watch = await baby_sit.WATCHES.get("acme/repo#7")
    assert watch is not None
    assert watch.active
    assert dispatch.await_count == 1

    checks[1] = _check_run(4, "tests", "failure")
    assert await baby_sit.evaluate_watch("acme/repo#7") == "stopped"


async def test_an_in_flight_failure_dispatch_holds_off_every_other_watch_writer(
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
    monkeypatch.setattr(baby_sit, "WATCH_LOCK_WAIT", timedelta(milliseconds=100))
    with pytest.raises(baby_sit.WatchBusy):
        await baby_sit.stop_watch("acme/repo#7", thread_id="thread-1")
    with pytest.raises(baby_sit.WatchBusy):
        await _start_watch()
    with pytest.raises(baby_sit.WatchBusy):
        await baby_sit.adopt_agent_server_cron("acme/repo#7")
    monkeypatch.setattr(baby_sit, "WATCH_LOCK_WAIT", timedelta(seconds=60))
    stop = asyncio.create_task(baby_sit.stop_watch("acme/repo#7", thread_id="thread-1"))
    await asyncio.sleep(0.3)
    assert not stop.done()
    release_dispatch.set()

    assert await first == "dispatched"
    dispatch.assert_awaited_once()
    assert await stop
    assert await baby_sit.WATCHES.get("acme/repo#7") is None


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

    with pytest.raises(baby_sit.BabySitError, match="Flaky rerun limit reached"):
        await baby_sit.record_retry(
            "acme/repo#7",
            thread_id="thread-1",
            head_sha="head-1",
            check_name="tests",
            evidence="runner timeout",
        )
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


async def test_only_the_watching_task_can_take_over_stop_or_record_retries(
    registry_db: None,
) -> None:
    watch = await _start_watch()
    assert watch.thread_id == "thread-1"
    task = await Task.for_thread("thread-1", title="", workspace="default")
    assert watch.task_id == task.id

    with pytest.raises(baby_sit.WatchOwnedElsewhere):
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
    with pytest.raises(baby_sit.WatchOwnedElsewhere):
        await baby_sit.record_retry(
            "acme/repo#7",
            thread_id="thread-2",
            head_sha="head-1",
            check_name="tests",
            evidence="runner timeout",
        )
    with pytest.raises(baby_sit.WatchOwnedElsewhere):
        await baby_sit.stop_watch("acme/repo#7", thread_id="thread-2")
    assert await baby_sit.WATCHES.get("acme/repo#7") is not None


async def test_a_failing_watch_lock_claim_skips_only_that_watch_on_a_ci_webhook(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _start_watch()
    monkeypatch.setattr(
        baby_sit.event_claims, "claim", AsyncMock(side_effect=RuntimeError("database unavailable"))
    )
    payload = {
        "repository": {"owner": {"login": "acme"}, "name": "repo"},
        "check_run": {"status": "completed", "conclusion": "failure", "head_sha": "head-1"},
    }

    assert await baby_sit.handle_ci_webhook(payload, "check_run") == {
        "matched": 1,
        "dispatched": 0,
    }


async def test_a_failing_watch_lock_claim_is_raised_not_waited_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = AsyncMock(side_effect=RuntimeError("database unavailable"))
    monkeypatch.setattr(baby_sit.event_claims, "claim", claim)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await baby_sit.stop_watch("acme/repo#7", thread_id="thread-1")

    claim.assert_awaited_once()


@pytest.mark.parametrize(
    "steps",
    [
        pytest.param([("h1", [], True, [])], id="no failures"),
        pytest.param([("h1", [_A_RUN], True, ["a"])], id="first failure while ci runs"),
        pytest.param([("h1", [_A_RUN], False, ["a"])], id="first failure after ci settles"),
        pytest.param(
            [
                ("h1", [_A_RUN], True, ["a"]),
                ("h1", [_A_RUN], True, []),
                ("h1", [_A_RUN], False, []),
            ],
            id="sent failure seen again",
        ),
        pytest.param(
            [
                ("h1", [_A_RUN], True, ["a"]),
                ("h1", [_A_RUN, _B_RUN], True, []),
                ("h1", [_A_RUN, _B_RUN], False, ["b"]),
            ],
            id="new failure waits for ci to settle",
        ),
        pytest.param(
            [
                ("h1", [_A_RUN], True, ["a"]),
                ("h1", [_A_RUN, _B_RUN], True, []),
                ("h1", [_A_RUN, _B_RUN, _C_RUN], True, []),
                ("h1", [_A_RUN, _B_RUN, _C_RUN], False, ["b", "c"]),
            ],
            id="failures one by one wake once at settle",
        ),
        pytest.param(
            [("h1", [_A_RUN, _B_RUN], False, ["a", "b"]), ("h1", [_A_RUN], False, [])],
            id="shrinking set",
        ),
        pytest.param(
            [
                ("h1", [_A_RUN], True, ["a"]),
                ("h1", [], True, []),
                ("h1", [_B_RUN], True, []),
                ("h1", [_B_RUN], False, ["b"]),
            ],
            id="recovered failure still waits for ci to settle",
        ),
        pytest.param(
            [
                ("h1", [_A_RUN], False, ["a"]),
                ("h1", [], True, []),
                ("h1", [_A_RERUN], False, ["a"]),
            ],
            id="rerun fails again",
        ),
        pytest.param(
            [("h1", [_A_RUN], False, ["a"]), ("h1", [], True, []), ("h1", [], False, [])],
            id="rerun passes",
        ),
        pytest.param(
            [("h1", [_DEPLOY_STATUS], False, ["deploy"]), ("h1", [_DEPLOY_STATUS], False, [])],
            id="status reposted",
        ),
        pytest.param(
            [
                ("h1", [_DEPLOY_STATUS], False, ["deploy"]),
                ("h1", [], False, []),
                ("h1", [_DEPLOY_STATUS], False, ["deploy"]),
            ],
            id="status recovers and fails again",
        ),
        pytest.param(
            [("h1", [_A_RUN], False, ["a"]), ("h1", [_A_RERUN], False, ["a"])],
            id="still red check rerun",
        ),
        pytest.param(
            [
                ("h1", [_DEPLOY_STATUS], False, ["deploy"]),
                ("h2", [_DEPLOY_STATUS], True, ["deploy"]),
            ],
            id="new commit",
        ),
        pytest.param(
            [
                ("h1", [_A_RUN], False, ["a"]),
                ("h1", [], False, []),
                ("h1", [_A_RERUN], True, []),
                ("h1", [_A_RERUN], False, ["a"]),
            ],
            id="rerun fails after a failure wake and green",
        ),
        pytest.param(
            [("h1", [], False, []), ("h1", [_A_RERUN], True, ["a"])],
            id="rerun fails after a green-only commit",
        ),
    ],
)
def test_failures_to_send(steps: list[_Step]) -> None:
    watch = baby_sit.BabySitWatch(key="acme/repo#7")

    for head_sha, failures, has_pending, expected in steps:
        watch.head_sha = head_sha
        watch.forget_recovered(failures)
        to_send = watch.failures_to_send(failures, has_pending=has_pending)
        if to_send:
            watch.record_sent(to_send)

        assert [failure.name for failure in to_send] == expected
