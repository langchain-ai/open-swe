import asyncio
from typing import Any
from unittest.mock import AsyncMock

import httpx2
import pytest
from langgraph_sdk.errors import ConflictError

from openswe import baby_sit
from openswe.github import app as github_app
from openswe.github.ci import RequiredCheck
from openswe.github.http import GitHubError, RepoClient
from openswe.github.pull_request_status import PullRequestClient
from openswe.slack.client import GitHubPrRef
from openswe.source_context import SourceContext
from openswe.tasks.store import Task


class _Crons:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.searches: list[dict[str, Any]] = []

    async def create(self, assistant_id: str, **kwargs: Any):
        self.created.append({"assistant_id": assistant_id, **kwargs})
        return {"cron_id": f"cron-{len(self.created)}"}

    async def search(self, **kwargs: Any):
        self.searches.append(kwargs)
        return []

    async def delete(self, cron_id: str) -> None:
        self.deleted.append(cron_id)


class _Threads:
    def __init__(self, active: set[str]) -> None:
        self.active = active
        self.deleted: list[str] = []
        self.create_lock = asyncio.Lock()

    async def create(self, *, thread_id: str, if_exists: str, ttl: int) -> None:
        assert if_exists == "raise"
        assert ttl == baby_sit.WATCH_LOCK_TTL_MINUTES
        async with self.create_lock:
            if thread_id in self.active:
                response = httpx2.Response(409, request=httpx2.Request("POST", "http://test"))
                raise ConflictError("already exists", response=response, body=None)
            self.active.add(thread_id)

    async def delete(self, thread_id: str) -> None:
        self.active.discard(thread_id)
        self.deleted.append(thread_id)


class _Client:
    def __init__(self, active_threads: set[str] | None = None) -> None:
        self.crons = _Crons()
        self.threads = _Threads(active_threads if active_threads is not None else set())


def _use_client(monkeypatch: pytest.MonkeyPatch, client: _Client) -> None:
    monkeypatch.setattr(baby_sit, "get_client", lambda: client)


@pytest.fixture
def watch_client(monkeypatch: pytest.MonkeyPatch, registry_db: None) -> _Client:
    client = _Client()
    _use_client(monkeypatch, client)
    return client


@pytest.fixture
def shared_watch_clients(registry_db: None) -> tuple[_Client, _Client]:
    active_threads: set[str] = set()
    return _Client(active_threads), _Client(active_threads)


async def _start_watch(client: _Client) -> baby_sit.BabySitWatch:
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


@pytest.mark.parametrize(
    ("read_method", "status_code", "body", "retryable"),
    [
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
    monkeypatch: pytest.MonkeyPatch,
    read_method: str,
    status_code: int | None,
    body: str,
    retryable: bool,
) -> None:
    client = _Client()
    _use_client(monkeypatch, client)
    watch = baby_sit.BabySitWatch(
        key="acme/repo#7",
        owner="acme",
        repo="repo",
        pr_number=7,
        pr_url="https://github.com/acme/repo/pull/7",
        head_sha="head-1",
        installation_id=42,
        cron_id="cron-1",
    )
    stored = {watch.key: watch.model_copy(deep=True)}

    async def save(current: baby_sit.BabySitWatch) -> baby_sit.BabySitWatch:
        stored[current.key] = current.model_copy(deep=True)
        return current

    monkeypatch.setattr(baby_sit.WATCHES, "get", AsyncMock(side_effect=stored.get))
    monkeypatch.setattr(baby_sit.WATCHES, "save", save)
    monkeypatch.setattr(baby_sit.WATCHES, "delete", AsyncMock(side_effect=stored.pop))
    monkeypatch.setattr(
        github_app, "get_github_app_installation_token", AsyncMock(return_value="t")
    )
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
    read = reads[read_method]
    read.side_effect = failure
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(baby_sit, "_notify_watch", notify)
    dispatch = AsyncMock()
    monkeypatch.setattr(baby_sit, "dispatch_agent_run", dispatch)

    for attempt in range(1, 4):
        result = await baby_sit.evaluate_watch(watch.key)
        if retryable or attempt < 3:
            assert result == "error"
            assert stored[watch.key].active
            assert stored[watch.key].evaluation_errors == (0 if retryable else attempt)
        else:
            assert result == "stopped"
            assert watch.key not in stored

    if retryable:
        assert client.crons.deleted == []
        notify.assert_not_awaited()
        dispatch.assert_not_awaited()
        read.side_effect = None
        assert await baby_sit.evaluate_watch(watch.key) == "pending"
        assert stored[watch.key].active
    else:
        assert client.crons.deleted == ["cron-1"]
        assert notify.await_args is not None
        message = notify.await_args.args[1]
        assert ("not found" if status_code == 404 else "denied access") in message


async def test_failure_dispatch_is_deduplicated_until_retry_is_recorded(
    watch_client: _Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _start_watch(watch_client)
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
    shared_watch_clients: tuple[_Client, _Client], monkeypatch: pytest.MonkeyPatch
) -> None:
    first_client, second_client = shared_watch_clients
    _use_client(monkeypatch, first_client)
    await _start_watch(first_client)
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
    _use_client(monkeypatch, second_client)
    assert await baby_sit.evaluate_watch("acme/repo#7") == "busy"
    release_dispatch.set()

    assert await first == "dispatched"
    dispatch.assert_awaited_once()
    _use_client(monkeypatch, first_client)
    assert await baby_sit.evaluate_watch("acme/repo#7") == "duplicate"


async def test_green_webhook_wakes_the_agent_and_stops_the_watch(
    watch_client: _Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _start_watch(watch_client)
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
    dispatch.assert_awaited_once()
    assert dispatch.await_args is not None
    assert "checks are green" in dispatch.await_args.args[1]
    notify.assert_not_awaited()
    assert await baby_sit.WATCHES.list_active() == []
    assert await baby_sit.WATCHES.get("acme/repo#7") is None


async def test_terminal_notification_falls_back_to_originating_agent_thread(
    watch_client: _Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch = await _start_watch(watch_client)
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
    watch_client: _Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _start_watch(watch_client)
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
    watch_client: _Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _start_watch(watch_client)
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
    watch_client: _Client,
) -> None:
    watch = await _start_watch(watch_client)
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
