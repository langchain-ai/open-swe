import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from langgraph_sdk.errors import ConflictError

from agent import baby_sit
from agent import store as agent_store
from agent.scheduling import tasks
from agent.utils import source_channel
from agent.utils.github_refs import GitHubPrRef


class _Store:
    def __init__(self, values: dict[str, dict[str, Any]] | None = None) -> None:
        self.values = values if values is not None else {}

    async def get_item(self, _namespace, key: str):
        value = self.values.get(key)
        return {"value": value} if value is not None else None

    async def put_item(self, _namespace, key: str, value: dict[str, Any]) -> None:
        self.values[key] = dict(value)

    async def delete_item(self, _namespace, key: str) -> None:
        self.values.pop(key, None)

    async def search_items(self, _namespace, *, filter, limit: int, offset: int):
        matches = [
            {"value": value}
            for value in self.values.values()
            if all(value.get(key) == expected for key, expected in filter.items())
        ]
        return {"items": matches[offset : offset + limit]}


class _Crons:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    async def create(self, assistant_id: str, **kwargs: Any):
        self.created.append({"assistant_id": assistant_id, **kwargs})
        return {"cron_id": f"cron-{len(self.created)}"}

    async def search(self, **_kwargs: Any):
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
                response = httpx.Response(409, request=httpx.Request("POST", "http://test"))
                raise ConflictError("already exists", response=response, body=None)
            self.active.add(thread_id)

    async def delete(self, thread_id: str) -> None:
        self.active.discard(thread_id)
        self.deleted.append(thread_id)


class _Client:
    def __init__(
        self,
        values: dict[str, dict[str, Any]] | None = None,
        active_threads: set[str] | None = None,
    ) -> None:
        self.store = _Store(values)
        self.crons = _Crons()
        self.threads = _Threads(active_threads if active_threads is not None else set())


def _use_client(monkeypatch: pytest.MonkeyPatch, client: _Client) -> None:
    monkeypatch.setattr(baby_sit, "in_process_langgraph_client", lambda: client)
    monkeypatch.setattr(agent_store, "store_client", lambda: client)


@pytest.fixture
def watch_client(monkeypatch: pytest.MonkeyPatch) -> _Client:
    client = _Client()
    _use_client(monkeypatch, client)
    return client


@pytest.fixture
def shared_watch_clients() -> tuple[_Client, _Client]:
    values: dict[str, dict[str, Any]] = {}
    active_threads: set[str] = set()
    return _Client(values, active_threads), _Client(values, active_threads)


async def _start_watch(client: _Client) -> baby_sit.BabySitWatch:
    return await baby_sit.start_watch(
        pr_ref=GitHubPrRef("Acme", "Repo", 7, "https://github.com/Acme/Repo/pull/7"),
        head_sha="head-1",
        head_ref="feature",
        installation_id=42,
        thread_id="thread-1",
        run_config={
            "thread_id": "thread-1",
            "source": "slack",
            "slack_thread": {"channel_id": "C1", "thread_ts": "1.2"},
        },
        source_context={"slack_thread": {"channel_id": "C1", "thread_ts": "1.2"}},
    )


async def test_watch_lifecycle_creates_and_deletes_ten_minute_cron(
    watch_client: _Client,
) -> None:
    watch = await _start_watch(watch_client)

    assert watch["key"] == "acme/repo#7"
    assert watch_client.threads.deleted == []
    assert watch_client.crons.created[0]["schedule"] == "*/10 * * * *"
    assert watch_client.crons.created[0]["input"] == {
        "task": "baby_sit",
        "payload": {"watch_key": "acme/repo#7"},
    }
    assert watch_client.crons.created[0]["config"] is None
    assert watch_client.crons.created[0]["metadata"] == {
        "kind": "baby_sit",
        "key": "acme/repo#7",
    }

    assert await baby_sit.stop_watch(watch["key"]) is True
    assert watch_client.crons.deleted == ["cron-1"]
    assert watch_client.store.values == {}


async def test_fallback_uses_watch_installation_token(
    watch_client: _Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _start_watch(watch_client)
    token = AsyncMock(return_value="t")
    monkeypatch.setattr(baby_sit, "get_github_app_installation_token", token)
    monkeypatch.setattr(
        baby_sit,
        "fetch_pr",
        AsyncMock(return_value={"state": "open", "head": {"sha": "head-1"}}),
    )
    monkeypatch.setattr(
        baby_sit,
        "list_check_runs",
        AsyncMock(return_value=[{"id": 1, "status": "in_progress"}]),
    )
    monkeypatch.setattr(baby_sit, "list_commit_statuses", AsyncMock(return_value=[]))

    assert await baby_sit.evaluate_watch("acme/repo#7") == "pending"
    token.assert_awaited_once_with(installation_id=42)


async def test_failure_dispatch_is_deduplicated_until_retry_is_recorded(
    watch_client: _Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _start_watch(watch_client)
    monkeypatch.setattr(baby_sit, "get_github_app_installation_token", AsyncMock(return_value="t"))
    monkeypatch.setattr(
        baby_sit,
        "fetch_pr",
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
    monkeypatch.setattr(baby_sit, "list_check_runs", list_checks)
    monkeypatch.setattr(baby_sit, "list_commit_statuses", AsyncMock(return_value=[]))
    dispatch = AsyncMock(return_value={"run_id": "run-1"})
    monkeypatch.setattr(baby_sit, "dispatch_agent_run", dispatch)

    assert await baby_sit.evaluate_watch("acme/repo#7") == "dispatched"
    assert dispatch.await_args is not None
    assert dispatch.await_args.kwargs["multitask_strategy"] == "enqueue"
    assert await baby_sit.evaluate_watch("acme/repo#7") == "duplicate"
    assert dispatch.await_count == 1

    monkeypatch.setattr(source_channel, "post_slack_thread_reply", AsyncMock(return_value=True))
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
    monkeypatch.setattr(baby_sit, "get_github_app_installation_token", AsyncMock(return_value="t"))
    monkeypatch.setattr(
        baby_sit,
        "fetch_pr",
        AsyncMock(return_value={"state": "open", "head": {"sha": "head-1"}}),
    )
    monkeypatch.setattr(
        baby_sit,
        "list_check_runs",
        AsyncMock(
            return_value=[
                {"id": 11, "name": "tests", "status": "completed", "conclusion": "failure"}
            ]
        ),
    )
    monkeypatch.setattr(baby_sit, "list_commit_statuses", AsyncMock(return_value=[]))
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


async def test_success_waits_for_stable_check_set_before_notifying(
    watch_client: _Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _start_watch(watch_client)
    monkeypatch.setattr(baby_sit, "get_github_app_installation_token", AsyncMock(return_value="t"))
    monkeypatch.setattr(
        baby_sit,
        "fetch_pr",
        AsyncMock(return_value={"state": "open", "head": {"sha": "head-1"}}),
    )
    monkeypatch.setattr(
        baby_sit,
        "list_check_runs",
        AsyncMock(
            return_value=[
                {"id": 1, "name": "tests", "status": "completed", "conclusion": "success"}
            ]
        ),
    )
    monkeypatch.setattr(baby_sit, "list_commit_statuses", AsyncMock(return_value=[]))
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(source_channel, "post_slack_thread_reply", notify)

    now = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(baby_sit, "_now", lambda: now)
    assert await baby_sit.evaluate_watch("acme/repo#7") == "settling"
    notify.assert_not_awaited()

    monkeypatch.setattr(
        baby_sit,
        "_now",
        lambda: now + timedelta(minutes=baby_sit.CHECK_SET_SETTLE_MINUTES),
    )
    assert await baby_sit.evaluate_watch("acme/repo#7") == "stopped"
    notify.assert_awaited_once()
    assert notify.await_args is not None
    assert notify.await_args.args[:2] == ("C1", "1.2")
    assert watch_client.store.values == {}


async def test_terminal_notification_falls_back_to_originating_agent_thread(
    watch_client: _Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch = await _start_watch(watch_client)
    watch["source_context"] = {}
    watch["run_config"] = {"thread_id": "thread-1", "source": "dashboard"}
    await baby_sit._put_watch(watch)
    monkeypatch.setattr(baby_sit, "get_github_app_installation_token", AsyncMock(return_value="t"))
    monkeypatch.setattr(
        baby_sit,
        "fetch_pr",
        AsyncMock(return_value={"state": "closed", "head": {"sha": "head-1"}}),
    )
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
    monkeypatch.setattr(source_channel, "post_slack_thread_reply", notify)

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
    monkeypatch.setattr(source_channel, "post_slack_thread_reply", AsyncMock(return_value=True))
    for _ in range(3):
        await baby_sit.record_retry(
            "acme/repo#7",
            thread_id="thread-1",
            head_sha="head-1",
            check_name="tests",
            evidence="runner timeout",
        )
    monkeypatch.setattr(baby_sit, "get_github_app_installation_token", AsyncMock(return_value="t"))
    monkeypatch.setattr(
        baby_sit,
        "fetch_pr",
        AsyncMock(return_value={"state": "open", "head": {"sha": "head-2"}}),
    )
    monkeypatch.setattr(
        baby_sit,
        "list_check_runs",
        AsyncMock(return_value=[{"id": 2, "name": "tests", "status": "in_progress"}]),
    )
    monkeypatch.setattr(baby_sit, "list_commit_statuses", AsyncMock(return_value=[]))

    assert await baby_sit.evaluate_watch("acme/repo#7") == "pending"
    watch = await baby_sit.get_watch("acme/repo#7")
    assert watch is not None
    assert watch["head_sha"] == "head-2"
    assert watch["retry_count"] == 0


async def test_failed_webhook_matches_active_head_and_deduplicates_delivery(
    watch_client: _Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _start_watch(watch_client)
    evaluate = AsyncMock(return_value="dispatched")
    monkeypatch.setattr(baby_sit, "_evaluate_watch", evaluate)
    token = AsyncMock(return_value="t")
    monkeypatch.setattr(baby_sit, "get_github_app_installation_token", token)
    payload = {
        "installation": {"id": 99},
        "repository": {"owner": {"login": "Acme"}, "name": "Repo"},
        "check_run": {"status": "completed", "conclusion": "failure", "head_sha": "head-1"},
    }

    first = await baby_sit.handle_ci_webhook(payload, "check_run", delivery_id="delivery-1")
    second = await baby_sit.handle_ci_webhook(payload, "check_run", delivery_id="delivery-1")
    payload["check_run"] = {
        "status": "completed",
        "conclusion": "failure",
        "head_sha": "head-2",
        "check_suite": {"head_branch": "feature"},
    }
    new_head = await baby_sit.handle_ci_webhook(payload, "check_run", delivery_id="delivery-2")

    assert first == {"matched": 1, "dispatched": 1}
    assert second == {"matched": 1, "dispatched": 0}
    assert new_head == {"matched": 1, "dispatched": 1}
    assert evaluate.await_count == 2
    token.assert_awaited_with(installation_id=99)
    watch = await baby_sit.get_watch("acme/repo#7")
    assert watch is not None
    assert watch["installation_id"] == 99


async def test_scheduler_routes_baby_sit_task(monkeypatch: pytest.MonkeyPatch) -> None:
    evaluate = AsyncMock(return_value="pending")
    monkeypatch.setattr(tasks, "evaluate_watch", evaluate)

    result = await tasks.run_scheduler_task("baby_sit", {"watch_key": "acme/repo#7"})

    assert result == {"status": "pending"}
    evaluate.assert_awaited_once_with("acme/repo#7")
