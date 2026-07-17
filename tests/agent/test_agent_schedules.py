from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from agent.dashboard import schedules
from agent.dashboard.schedules import ScheduleCreateBody, ScheduleUpdateBody


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}
        self.deleted: list[tuple[tuple[str, ...], str]] = []

    async def get_item(self, namespace: list[str], key: str) -> dict[str, Any] | None:
        value = self.items.get((tuple(namespace), key))
        return {"value": value} if value is not None else None

    async def put_item(self, namespace: list[str], key: str, value: dict[str, Any]) -> None:
        self.items[(tuple(namespace), key)] = value

    async def delete_item(self, namespace: list[str], key: str) -> None:
        self.deleted.append((tuple(namespace), key))
        self.items.pop((tuple(namespace), key), None)

    async def search_items(
        self,
        namespace: list[str],
        filter: dict[str, Any] | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> dict[str, Any]:
        values = [
            value
            for (stored_namespace, _), value in self.items.items()
            if stored_namespace == tuple(namespace)
        ]
        if filter:
            values = [
                value
                for value in values
                if all(value.get(key) == expected for key, expected in filter.items())
            ]
        return {"items": [{"value": value} for value in values[offset : offset + limit]]}


class _FakeCrons:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    async def create(self, assistant_id: str, **kwargs: Any) -> dict[str, Any]:
        self.created.append({"assistant_id": assistant_id, **kwargs})
        return {"cron_id": f"cron_{len(self.created)}"}

    async def delete(self, cron_id: str) -> None:
        self.deleted.append(cron_id)


class _FakeThreads:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> None:
        self.created.append(kwargs)

    async def update(self, **kwargs: Any) -> None:
        self.updated.append(kwargs)


class _FakeRuns:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create(self, thread_id: str, assistant_id: str, **kwargs: Any) -> dict[str, Any]:
        self.created.append({"thread_id": thread_id, "assistant_id": assistant_id, **kwargs})
        return {"run_id": "run_123"}


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()
        self.crons = _FakeCrons()
        self.threads = _FakeThreads()
        self.runs = _FakeRuns()


@pytest.fixture
def fake_client(monkeypatch) -> _FakeClient:  # noqa: ANN001
    client = _FakeClient()
    monkeypatch.setattr(schedules, "_client", lambda: client)
    return client


@pytest.fixture
def auth(monkeypatch) -> None:  # noqa: ANN001
    async def fake_get_valid_access_token(login: str) -> str:
        return "gho_token"

    async def fake_get_profile(login: str) -> dict[str, Any]:
        return {"base_branch": "main", "branch_prefix": "open-swe"}

    async def fake_resolve_run_email(login: str, profile: dict[str, Any]) -> str:
        return "alice@example.com"

    async def fake_repo_config_for_user(login: str, full_name: str | None) -> dict[str, str] | None:
        if not full_name:
            return None
        owner, name = full_name.split("/", 1)
        return {"owner": owner, "name": name}

    async def fake_require_repo_access_for_user(login: str, full_name: str) -> str:
        return "gho_token"

    monkeypatch.setattr(schedules, "get_valid_access_token", fake_get_valid_access_token)
    monkeypatch.setattr(schedules, "get_profile", fake_get_profile)
    monkeypatch.setattr(schedules, "_resolve_run_email", fake_resolve_run_email)
    monkeypatch.setattr(schedules, "repo_config_for_user", fake_repo_config_for_user)
    monkeypatch.setattr(
        schedules, "require_repo_access_for_user", fake_require_repo_access_for_user
    )


def test_cron_validation_rejects_non_five_field_expression() -> None:
    with pytest.raises(ValidationError):
        ScheduleCreateBody(prompt="hello", schedule="0 9 * *")


def test_cron_validation_accepts_steps_ranges_and_lists() -> None:
    body = ScheduleCreateBody(prompt="hello", schedule="*/15 9-17 * * 1,3,5")

    assert body.schedule == "*/15 9-17 * * 1,3,5"


def test_slack_channel_validation_normalizes_ids() -> None:
    body = ScheduleCreateBody(
        prompt="hello", schedule="0 9 * * *", slack_channel_id=" c0123456789 "
    )

    assert body.slack_channel_id == "C0123456789"
    with pytest.raises(ValidationError):
        ScheduleCreateBody(prompt="hello", schedule="0 9 * * *", slack_channel_id="#general")


async def test_create_agent_schedule_registers_scheduler_cron(fake_client, auth) -> None:  # noqa: ANN001, ARG001
    body = ScheduleCreateBody(
        name="Daily report",
        prompt="Summarize merged PRs",
        schedule="0 9 * * 1-5",
        repo="langchain-ai/open-swe",
        slack_channel_id="C0123456789",
    )

    result = await schedules.create_agent_schedule("alice", body, email="alice@example.com")

    assert result["name"] == "Daily report"
    assert result["enabled"] is True
    assert result["slackChannelId"] == "C0123456789"
    assert result["cronId"] == "cron_1"
    created = fake_client.crons.created[0]
    assert created["assistant_id"] == "scheduler"
    assert created["schedule"] == "0 9 * * 1-5"
    assert created["input"]["schedule_id"] == result["id"]
    assert created["config"]["configurable"]["schedule_id"] == result["id"]
    assert created["metadata"]["kind"] == "agent_schedule"


async def test_create_agent_schedule_requires_dashboard_token(fake_client, monkeypatch) -> None:  # noqa: ANN001, ARG001
    async def no_token(login: str) -> None:
        return None

    monkeypatch.setattr(schedules, "get_valid_access_token", no_token)

    with pytest.raises(HTTPException) as exc:
        await schedules.create_agent_schedule(
            "alice", ScheduleCreateBody(prompt="hello", schedule="0 9 * * 1")
        )

    assert exc.value.status_code == 401
    assert fake_client.crons.created == []


async def test_create_agent_schedule_requires_repo_access(fake_client, auth, monkeypatch) -> None:  # noqa: ANN001, ARG001
    async def deny_repo(login: str, full_name: str | None) -> dict[str, str] | None:
        raise HTTPException(403, "no access to this private repository")

    monkeypatch.setattr(schedules, "repo_config_for_user", deny_repo)

    with pytest.raises(HTTPException) as exc:
        await schedules.create_agent_schedule(
            "alice",
            ScheduleCreateBody(
                prompt="hello",
                schedule="0 9 * * 1",
                repo="victim/private",
            ),
        )

    assert exc.value.status_code == 403
    assert fake_client.crons.created == []


async def test_list_agent_schedules_uses_owner_filters_and_paginates(fake_client) -> None:  # noqa: ANN001
    for i in range(125):
        await fake_client.store.put_item(
            schedules.SCHEDULES_NAMESPACE,
            f"alice_{i}",
            {
                "id": f"alice_{i}",
                "name": f"Alice {i}",
                "prompt": "Run daily",
                "schedule": "0 9 * * *",
                "repo": None,
                "model": "Default",
                "enabled": True,
                "created_by": "alice",
                "user_email": "alice@example.com",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": f"2026-01-01T00:{i % 60:02d}:00+00:00",
            },
        )
    await fake_client.store.put_item(
        schedules.SCHEDULES_NAMESPACE,
        "bob_1",
        {
            "id": "bob_1",
            "name": "Bob",
            "prompt": "Run daily",
            "schedule": "0 9 * * *",
            "repo": None,
            "model": "Default",
            "enabled": True,
            "created_by": "bob",
            "user_email": "bob@example.com",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
    )

    result = await schedules.list_agent_schedules("alice", email="alice@example.com")

    assert len(result) == 125
    assert {item["id"] for item in result} == {f"alice_{i}" for i in range(125)}


async def test_update_agent_schedule_rechecks_repo_access(fake_client, auth, monkeypatch) -> None:  # noqa: ANN001, ARG001
    record = {
        "id": "sched_1",
        "name": "Daily",
        "prompt": "Run daily",
        "schedule": "0 9 * * *",
        "repo": None,
        "model": "Default",
        "effort": None,
        "enabled": True,
        "cron_id": "cron_old",
        "created_by": "alice",
        "user_email": "alice@example.com",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    await fake_client.store.put_item(schedules.SCHEDULES_NAMESPACE, "sched_1", record)

    async def repo_config(login: str, full_name: str | None) -> dict[str, str] | None:
        assert full_name == "langchain-ai/open-swe"
        return {"owner": "langchain-ai", "name": "open-swe"}

    monkeypatch.setattr(schedules, "repo_config_for_user", repo_config)

    result = await schedules.update_agent_schedule(
        "sched_1",
        "alice",
        ScheduleUpdateBody(repo="langchain-ai/open-swe"),
        email="alice@example.com",
    )

    assert result["repo"] == "langchain-ai/open-swe"


async def test_update_agent_schedule_clears_slack_channel(fake_client) -> None:  # noqa: ANN001
    record = {
        "id": "sched_1",
        "name": "Daily",
        "prompt": "Run daily",
        "schedule": "0 9 * * *",
        "repo": None,
        "slack_channel_id": "C0123456789",
        "model": "Default",
        "effort": None,
        "enabled": True,
        "cron_id": "cron_old",
        "created_by": "alice",
        "user_email": "alice@example.com",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    await fake_client.store.put_item(schedules.SCHEDULES_NAMESPACE, "sched_1", record)

    result = await schedules.update_agent_schedule(
        "sched_1",
        "alice",
        ScheduleUpdateBody(slack_channel_id=None),
        email="alice@example.com",
    )

    assert result["slackChannelId"] is None


async def test_update_agent_schedule_pause_deletes_cron(fake_client) -> None:  # noqa: ANN001
    record = {
        "id": "sched_1",
        "name": "Daily",
        "prompt": "Run daily",
        "schedule": "0 9 * * *",
        "repo": None,
        "model": "Default",
        "effort": None,
        "enabled": True,
        "cron_id": "cron_old",
        "created_by": "alice",
        "user_email": "alice@example.com",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    await fake_client.store.put_item(schedules.SCHEDULES_NAMESPACE, "sched_1", record)

    result = await schedules.update_agent_schedule(
        "sched_1", "alice", ScheduleUpdateBody(enabled=False), email="alice@example.com"
    )

    assert result["enabled"] is False
    assert result["cronId"] is None
    assert fake_client.crons.deleted == ["cron_old"]


async def test_launch_scheduled_agent_run_skips_when_repo_access_revoked(
    fake_client, auth, monkeypatch
) -> None:  # noqa: ANN001, ARG001
    record = {
        "id": "sched_1",
        "name": "Weekly dependencies",
        "prompt": "Check dependencies and open a PR if needed",
        "schedule": "0 9 * * 1",
        "repo": {"owner": "langchain-ai", "name": "open-swe"},
        "model": "Default",
        "effort": None,
        "base_branch": "main",
        "branch_prefix": "open-swe",
        "enabled": True,
        "cron_id": "cron_1",
        "created_by": "alice",
        "user_email": "alice@example.com",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    await fake_client.store.put_item(schedules.SCHEDULES_NAMESPACE, "sched_1", record)

    async def deny_access(login: str, full_name: str) -> str:
        raise HTTPException(403, "no access to this private repository")

    monkeypatch.setattr(schedules, "require_repo_access_for_user", deny_access)

    result = await schedules.launch_scheduled_agent_run("sched_1")

    assert result == {
        "status": "unauthorized",
        "schedule_id": "sched_1",
        "error": "no access to this private repository",
    }
    assert fake_client.runs.created == []
    stored = fake_client.store.items[(tuple(schedules.SCHEDULES_NAMESPACE), "sched_1")]
    assert stored["last_error"] == "no access to this private repository"


async def test_launch_scheduled_agent_run_starts_fresh_agent_thread(fake_client, auth) -> None:  # noqa: ANN001, ARG001
    record = {
        "id": "sched_1",
        "name": "Weekly dependencies",
        "prompt": "Check dependencies and open a PR if needed",
        "schedule": "0 9 * * 1",
        "repo": {"owner": "langchain-ai", "name": "open-swe"},
        "model": "Default",
        "effort": None,
        "base_branch": "main",
        "branch_prefix": "open-swe",
        "enabled": True,
        "cron_id": "cron_1",
        "created_by": "alice",
        "user_email": "alice@example.com",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    await fake_client.store.put_item(schedules.SCHEDULES_NAMESPACE, "sched_1", record)

    result = await schedules.launch_scheduled_agent_run("sched_1")

    assert result["status"] == "started"
    thread_id = result["thread_id"]
    assert fake_client.threads.created[0]["thread_id"] == thread_id
    metadata = fake_client.threads.created[0]["metadata"]
    assert metadata["source"] == "schedule"
    assert metadata["repo_owner"] == "langchain-ai"
    assert metadata["repo_name"] == "open-swe"
    run = fake_client.runs.created[0]
    assert run["thread_id"] == thread_id
    assert run["assistant_id"] == "agent"
    assert run["input"]["messages"][0]["content"] == record["prompt"]
    assert run["durability"] == "sync"
    assert run["multitask_strategy"] == "interrupt"
    assert run["if_not_exists"] == "create"
    assert run["config"]["configurable"]["source"] == "schedule"
    assert run["config"]["configurable"]["repo"] == record["repo"]

    stored = fake_client.store.items[(tuple(schedules.SCHEDULES_NAMESPACE), "sched_1")]
    assert stored["last_thread_id"] == thread_id
    assert stored["last_run_id"] == "run_123"


async def test_launch_scheduled_agent_run_connects_slack_thread(
    fake_client, auth, monkeypatch
) -> None:  # noqa: ANN001, ARG001
    record = {
        "id": "sched_1",
        "name": "Linear queue",
        "prompt": "Work the next Linear issue",
        "schedule": "*/15 * * * *",
        "repo": {"owner": "langchain-ai", "name": "open-swe"},
        "slack_channel_id": "C0123456789",
        "model": "Default",
        "effort": None,
        "base_branch": "main",
        "branch_prefix": "open-swe",
        "enabled": True,
        "cron_id": "cron_1",
        "created_by": "alice",
        "user_email": "alice@example.com",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    await fake_client.store.put_item(schedules.SCHEDULES_NAMESPACE, "sched_1", record)
    posted: dict[str, Any] = {}

    async def fake_post(channel_id: str, text: str, **kwargs: Any) -> tuple[str, None]:
        posted.update({"channel_id": channel_id, "text": text, "kwargs": kwargs})
        return "1784302353.900029", None

    monkeypatch.setattr(schedules, "post_slack_top_level_message_with_ts", fake_post)

    result = await schedules.launch_scheduled_agent_run("sched_1")

    expected_thread_id = schedules.generate_thread_id_from_slack_thread(
        "C0123456789", "1784302353.900029"
    )
    assert result["thread_id"] == expected_thread_id
    assert posted["channel_id"] == "C0123456789"
    assert "Linear queue" in posted["text"]
    metadata = fake_client.threads.created[0]["metadata"]
    slack_thread = metadata["source_context"]["slack_thread"]
    assert slack_thread["channel_id"] == "C0123456789"
    assert slack_thread["thread_ts"] == "1784302353.900029"
    run = fake_client.runs.created[0]
    assert run["config"]["configurable"]["slack_thread"] == slack_thread
    assert "slack_thread_reply" in run["input"]["messages"][0]["content"]
    mapping = fake_client.store.items[
        (("slack_run_map", "C0123456789"), "thread:1784302353.900029")
    ]
    assert mapping["run_id"] == "run_123"


async def test_launch_scheduled_agent_run_stops_when_slack_post_fails(
    fake_client, auth, monkeypatch
) -> None:  # noqa: ANN001, ARG001
    record = {
        "id": "sched_1",
        "name": "Linear queue",
        "prompt": "Work the next Linear issue",
        "schedule": "*/15 * * * *",
        "repo": None,
        "slack_channel_id": "C0123456789",
        "model": "Default",
        "effort": None,
        "enabled": True,
        "cron_id": "cron_1",
        "created_by": "alice",
        "user_email": "alice@example.com",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    await fake_client.store.put_item(schedules.SCHEDULES_NAMESPACE, "sched_1", record)

    async def fake_post(*args: Any, **kwargs: Any) -> tuple[None, str]:
        return None, "not_in_channel"

    monkeypatch.setattr(schedules, "post_slack_top_level_message_with_ts", fake_post)

    result = await schedules.launch_scheduled_agent_run("sched_1")

    assert result == {
        "status": "error",
        "schedule_id": "sched_1",
        "error": "Slack post failed: not_in_channel",
    }
    assert fake_client.threads.created == []
    assert fake_client.runs.created == []
    stored = fake_client.store.items[(tuple(schedules.SCHEDULES_NAMESPACE), "sched_1")]
    assert stored["last_error"] == "Slack post failed: not_in_channel"
