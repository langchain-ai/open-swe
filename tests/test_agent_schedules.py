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

    async def search_items(self, namespace: list[str], limit: int = 1000) -> dict[str, Any]:
        items = [
            {"value": value}
            for (stored_namespace, _), value in self.items.items()
            if stored_namespace == tuple(namespace)
        ]
        return {"items": items[:limit]}


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

    monkeypatch.setattr(schedules, "get_valid_access_token", fake_get_valid_access_token)
    monkeypatch.setattr(schedules, "get_profile", fake_get_profile)
    monkeypatch.setattr(schedules, "_resolve_run_email", fake_resolve_run_email)


def test_cron_validation_rejects_non_five_field_expression() -> None:
    with pytest.raises(ValidationError):
        ScheduleCreateBody(prompt="hello", schedule="0 9 * *")


def test_cron_validation_accepts_steps_ranges_and_lists() -> None:
    body = ScheduleCreateBody(prompt="hello", schedule="*/15 9-17 * * 1,3,5")

    assert body.schedule == "*/15 9-17 * * 1,3,5"


async def test_create_agent_schedule_registers_scheduler_cron(fake_client, auth) -> None:  # noqa: ANN001, ARG001
    body = ScheduleCreateBody(
        name="Daily report",
        prompt="Summarize merged PRs",
        schedule="0 9 * * 1-5",
        repo="langchain-ai/open-swe",
    )

    result = await schedules.create_agent_schedule("alice", body, email="alice@example.com")

    assert result["name"] == "Daily report"
    assert result["enabled"] is True
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


async def test_launch_scheduled_agent_run_starts_fresh_agent_thread(fake_client) -> None:  # noqa: ANN001
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
    assert run["config"]["configurable"]["source"] == "schedule"
    assert run["config"]["configurable"]["repo"] == record["repo"]

    stored = fake_client.store.items[(tuple(schedules.SCHEDULES_NAMESPACE), "sched_1")]
    assert stored["last_thread_id"] == thread_id
    assert stored["last_run_id"] == "run_123"
