"""Rollout watches poll locate_commit and wake the merged thread only on a change."""

import asyncio
import importlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import httpx2
import pytest
from langgraph_sdk.errors import ConflictError

from agent import rollouts, scheduler
from agent import store as agent_store

record_tool = importlib.import_module("agent.tools.record_rollout_check")
page_tool = importlib.import_module("agent.tools.rollout_page_check")

SHA = "a" * 40


class _Store:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, Any]] = {}

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
            if all(value.get(field) == expected for field, expected in filter.items())
        ]
        return {"items": matches[offset : offset + limit]}


class _Crons:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    async def create(self, assistant_id: str, **kwargs: Any):
        self.created.append({"assistant_id": assistant_id, **kwargs})
        return {"cron_id": f"cron-{len(self.created)}"}

    async def search(self, **kwargs: Any):
        return []

    async def delete(self, cron_id: str) -> None:
        self.deleted.append(cron_id)


class _Threads:
    def __init__(self) -> None:
        self.active: set[str] = set()
        self.updated: list[dict[str, Any]] = []
        self.records: dict[str, dict[str, Any]] = {}
        self.create_lock = asyncio.Lock()

    async def create(self, *, thread_id: str, if_exists: str, ttl: int) -> None:
        assert if_exists == "raise"
        assert ttl == rollouts.WATCH_LOCK_TTL_MINUTES
        async with self.create_lock:
            if thread_id in self.active:
                response = httpx2.Response(409, request=httpx2.Request("POST", "http://test"))
                raise ConflictError("already exists", response=response, body=None)
            self.active.add(thread_id)

    async def delete(self, thread_id: str) -> None:
        self.active.discard(thread_id)

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        self.updated.append({"thread_id": thread_id, "metadata": metadata})

    async def get(self, thread_id: str) -> dict[str, Any]:
        return self.records.get(thread_id, {"metadata": {}})


class _Client:
    def __init__(self) -> None:
        self.store = _Store()
        self.crons = _Crons()
        self.threads = _Threads()


def _targets(*, dev: bool, staging: bool) -> dict[str, Any]:
    return {
        "targets": [
            {"id": "gcp-dev", "label": "GCP Dev", "contains": dev, "error": ""},
            {"id": "gcp-staging", "label": "GCP Staging", "contains": staging, "error": ""},
            {"id": "aws-self-hosted", "label": "AWS Self-Hosted", "contains": True, "error": ""},
        ]
    }


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> _Client:
    fake = _Client()
    monkeypatch.setattr(rollouts, "get_client", lambda: fake)
    monkeypatch.setattr(agent_store, "store_client", lambda: fake)
    return fake


def test_envs_ready_waits_for_every_prod_region_and_skips_self_hosted() -> None:
    targets: list[dict[str, Any]] = [
        {"id": "gcp-dev", "label": "GCP Dev", "contains": True, "error": ""},
        {"id": "gcp-staging", "label": "GCP Staging", "contains": False, "error": ""},
        {"id": "gcp-us-prod", "label": "GCP US Prod", "contains": True, "error": ""},
        {"id": "gcp-eu-prod", "label": "GCP EU Prod", "contains": True, "error": "unavailable"},
        {"id": "self-hosted-main", "label": "Self-hosted main", "contains": True, "error": ""},
    ]
    assert rollouts.envs_ready(targets) == {"dev"}

    targets[3] = {**targets[3], "error": ""}
    targets.extend(
        [
            {"id": "gcp-apac-prod", "label": "GCP APAC Prod", "contains": True, "error": ""},
            {"id": "aws-us-prod", "label": "AWS US Prod", "contains": True, "error": ""},
        ]
    )
    assert rollouts.envs_ready(targets) == {"dev", "prod"}


async def test_dev_wakes_once_and_staging_waits_a_poll(
    client: _Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    await rollouts.start_watch(
        thread_id="thread-1",
        owner="acme",
        repo="repo",
        pr_number=7,
        sha=SHA,
        author="octocat",
        envs=["dev", "staging"],
        page="projects",
        expected="the empty state is gone",
        metrics="p95 latency",
        resolves_thread=True,
        run_config={"thread_id": "thread-1", "source": "slack", "workspace": "oss"},
        source_context={"slack_thread": {"channel_id": "C1", "thread_ts": "1.2"}},
    )
    assert client.crons.created[0]["schedule"] == "*/15 * * * *"
    assert client.crons.created[0]["input"] == {"task": "rollout", "watch_key": "acme/repo#7"}
    monkeypatch.setattr(
        rollouts, "locate_commit", AsyncMock(return_value=_targets(dev=True, staging=True))
    )
    dispatch = AsyncMock(return_value={"run_id": "run-1"})
    monkeypatch.setattr(rollouts, "dispatch_agent_run", dispatch)

    assert await rollouts.evaluate_rollout("acme/repo#7") == "waiting"
    assert dispatch.await_count == 1
    dev_reply = dispatch.await_args.args[1]
    assert "Do not query Datadog" in dev_reply
    assert "gh pr comment" not in dev_reply
    assert "slack_reply" in dev_reply
    assert "langsmith-releases" not in dev_reply
    assert dispatch.await_args.kwargs["multitask_strategy"] == "enqueue"
    assert dispatch.await_args.kwargs["source_context"].slack_thread.channel_id == "C1"

    assert await rollouts.evaluate_rollout("acme/repo#7") == "done"
    assert dispatch.await_count == 2
    verdict = dispatch.await_args_list[1].args[1]
    assert "staging" in verdict
    assert "gh pr comment" in verdict
    assert "Query env:staging." in verdict
    assert "slack_reply" in verdict
    assert "langsmith-releases" not in verdict
    for tag in (
        "env:dev",
        "env:staging",
        "env:prod",
        "env:eu-prod",
        "env:apac-prod",
        "env:aws-prod",
    ):
        assert tag in verdict
    watch = await rollouts.WATCHES.get("acme/repo#7")
    assert watch is not None and watch.active is False
    assert client.threads.updated[-1]["metadata"]["resolved"] is True
    assert client.threads.updated[-1]["metadata"]["rollout_status"] == "done"


async def test_expired_watch_does_not_query_locate(
    client: _Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    await rollouts.start_watch(
        thread_id="thread-1",
        owner="acme",
        repo="repo",
        pr_number=7,
        sha=SHA,
        author="octocat",
        envs=["dev", "staging"],
        page="",
        expected="",
        metrics="",
        resolves_thread=False,
        run_config={"workspace": "oss"},
        source_context={},
    )
    watch = await rollouts.WATCHES.get("acme/repo#7")
    assert watch is not None
    watch.created_at = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    await rollouts.WATCHES.save(watch)

    async def fail_locate(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("locate should not run after the watch expires")

    monkeypatch.setattr(rollouts, "locate_commit", fail_locate)
    dispatch = AsyncMock(return_value={"run_id": "run-1"})
    monkeypatch.setattr(rollouts, "dispatch_agent_run", dispatch)

    assert await rollouts.evaluate_rollout("acme/repo#7") == "expired"
    assert "7 days" in dispatch.await_args.args[1]
    assert client.crons.deleted == ["cron-1"]


async def test_locate_commit_accepts_a_json_string(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Tool:
        metadata = {"mcp_tool_name": "releases.locate_commit"}

        async def ainvoke(self, arguments: dict[str, str]) -> str:
            assert arguments == {"commit": SHA}
            return json.dumps(
                {"targets": [{"id": "gcp-dev", "label": "GCP Dev", "contains": True, "error": ""}]}
            )

    monkeypatch.setattr("agent.mcp.instance.instance_mcp_source", lambda: object())
    monkeypatch.setattr("agent.mcp.workspace.workspace_mcp_source", lambda _workspace: object())
    monkeypatch.setattr("agent.mcp.runtime.load_mcp_tools", AsyncMock(return_value=[_Tool()]))

    report = await rollouts.locate_commit("oss", SHA)

    assert report is not None
    assert report["targets"][0]["contains"] is True


def test_done_status_does_not_cover_a_newer_check() -> None:
    assert rollouts.rollout_watch_pending(
        {
            "rollout_check": {"check_id": "newer"},
            "rollout_status": "done",
            "rollout_status_check_id": "older",
        }
    )
    assert not rollouts.rollout_watch_pending(
        {
            "rollout_check": {"check_id": "older"},
            "rollout_status": "done",
            "rollout_status_check_id": "older",
        }
    )


async def test_older_watch_stops_without_closing_a_newer_check(
    client: _Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    await rollouts.start_watch(
        thread_id="thread-1",
        owner="acme",
        repo="repo",
        pr_number=7,
        sha=SHA,
        author="octocat",
        envs=["dev", "staging"],
        page="",
        expected="",
        metrics="",
        resolves_thread=True,
        run_config={"workspace": "oss"},
        source_context={},
        check_id="older",
    )
    client.threads.records["thread-1"] = {"metadata": {"rollout_check": {"check_id": "newer"}}}

    async def fail_locate(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("a superseded watch should not poll")

    monkeypatch.setattr(rollouts, "locate_commit", fail_locate)
    assert await rollouts.evaluate_rollout("acme/repo#7") == "superseded"
    assert all(item["metadata"].get("rollout_status") != "done" for item in client.threads.updated)
    assert all("resolved" not in item["metadata"] for item in client.threads.updated)
    watch = await rollouts.WATCHES.get("acme/repo#7")
    assert watch is not None and watch.active is False


async def test_start_from_merge_uses_the_merge_sha(client: _Client) -> None:
    await rollouts.start_from_merge(
        "thread-1",
        {
            "kind": "agent",
            "rollout_check": {
                "envs": ["dev", "staging"],
                "page": "projects",
                "expected": "the chart renders",
                "metrics": "",
                "author": "octocat",
                "run_config": {"workspace": "oss", "source": "slack"},
                "source_context": {},
            },
            "pull_requests": [{"resolves_thread": True}],
        },
        {
            "repository": {"full_name": "langchain-ai/langchainplus"},
            "pull_request": {
                "number": 7,
                "merge_commit_sha": SHA,
                "user": {"login": "octocat"},
            },
        },
    )
    watch = await rollouts.WATCHES.get("langchain-ai/langchainplus#7")
    assert watch is not None
    assert watch.sha == SHA
    assert watch.workspace == "oss"
    assert watch.resolves_thread is True
    assert client.threads.updated[-1]["metadata"] == {"rollout_status": "watching"}


async def test_start_from_merge_ignores_other_repositories(client: _Client) -> None:
    await rollouts.start_from_merge(
        "thread-1",
        {"kind": "agent", "rollout_check": {"envs": ["dev", "staging"]}},
        {
            "repository": {"full_name": "langchain-ai/open-swe"},
            "pull_request": {"number": 7, "merge_commit_sha": SHA, "user": {"login": "octocat"}},
        },
    )
    assert client.store.values == {}


async def test_start_from_merge_ignores_a_thread_without_a_check(client: _Client) -> None:
    await rollouts.start_from_merge("thread-1", {"kind": "agent"}, {"pull_request": {}})
    assert client.store.values == {}


async def test_scheduler_tick_runs_the_rollout_watch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scheduler, "evaluate_rollout", AsyncMock(return_value="waiting"))
    result = await scheduler._launch(
        scheduler.SchedulerState(task="rollout", watch_key="acme/repo#7"), {}
    )
    assert result == {"result": {"status": "waiting"}}
    scheduler.evaluate_rollout.assert_awaited_once_with("acme/repo#7")


async def test_record_rollout_check_keeps_the_wake_context_and_drops_url_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates: list[dict[str, Any]] = []

    class _Threads:
        async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
            assert thread_id == "thread-1"
            updates.append(metadata)

    class _LangGraph:
        threads = _Threads()

    monkeypatch.setattr(
        record_tool,
        "get_config",
        lambda: {
            "configurable": {
                "thread_id": "thread-1",
                "github_login": "octocat",
                "workspace": "oss",
                "repo": {"owner": "langchain-ai", "name": "langchainplus"},
                "source": "slack",
                "slack_thread": {"channel_id": "C1", "thread_ts": "1.2"},
            }
        },
    )
    monkeypatch.setattr(record_tool, "get_client", lambda: _LangGraph())
    secret = "rollout-bot-test-secret"

    result = await record_tool.record_rollout_check(
        page=f"https://user:{secret}@smith.langchain.com/o/org",
        expected="the chart renders",
        metrics="p95 latency",
    )

    assert result["envs"] == ["dev", "staging"]
    check = updates[0]["rollout_check"]
    assert check["envs"] == ["dev", "staging"]
    assert isinstance(check["check_id"], str) and check["check_id"]
    assert secret not in json.dumps(check)
    assert check["run_config"]["workspace"] == "oss"
    assert check["run_config"]["slack_thread"]["channel_id"] == "C1"
    assert check["source_context"]["slack_thread"]["channel_id"] == "C1"
    assert updates[0]["rollout_status"] is None
    assert result["watched"] is True


async def test_record_rollout_check_skips_other_repositories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updated = False

    class _Threads:
        async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
            del thread_id, metadata
            nonlocal updated
            updated = True

    class _LangGraph:
        threads = _Threads()

    monkeypatch.setattr(
        record_tool,
        "get_config",
        lambda: {
            "configurable": {"thread_id": "thread-1", "repo": {"owner": "acme", "name": "repo"}}
        },
    )
    monkeypatch.setattr(record_tool, "get_client", lambda: _LangGraph())

    result = await record_tool.record_rollout_check(page="projects")

    assert result["watched"] is False
    assert updated is False


async def test_page_check_reports_config_without_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "rollout-bot-test-secret"
    monkeypatch.delenv("ROLLOUT_BOT_USERNAME", raising=False)
    monkeypatch.setenv("ROLLOUT_BOT_EMAIL", "openswe@example.com")
    monkeypatch.setenv("ROLLOUT_BOT_PASSWORD", secret)

    rejected = await page_tool.rollout_page_check(
        f"https://user:{secret}@smith.langchain.com/o/org", "projects"
    )
    assert rejected == {"ok": False, "reason": "invalid_url"}
    assert secret not in json.dumps(rejected)

    unavailable = await page_tool.rollout_page_check(
        "https://smith.langchain.com/o/org", "projects"
    )
    assert unavailable["reason"] == "browser_unavailable"
    assert unavailable["email_configured"] is True
    assert unavailable["password_configured"] is True
    assert "username_configured" not in unavailable
    assert secret not in json.dumps(unavailable)

    monkeypatch.delenv("ROLLOUT_BOT_PASSWORD")
    missing = await page_tool.rollout_page_check("https://smith.langchain.com/o/org", "projects")
    assert missing == {
        "ok": False,
        "reason": "credentials_missing",
        "missing": ["ROLLOUT_BOT_PASSWORD"],
    }
