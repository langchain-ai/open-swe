from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent import delivery_queue as queue
from agent import scheduler


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: list[str], key: str) -> dict[str, Any] | None:
        value = self.items.get((tuple(namespace), key))
        return {"value": value} if value is not None else None

    async def put_item(self, namespace: list[str], key: str, value: dict[str, Any]) -> None:
        self.items[(tuple(namespace), key)] = value

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

    async def create(self, assistant_id: str, **kwargs: Any) -> dict[str, Any]:
        self.created.append({"assistant_id": assistant_id, **kwargs})
        return {"cron_id": f"cron_{len(self.created)}"}


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()
        self.crons = _FakeCrons()


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    client = _FakeClient()
    monkeypatch.setattr(queue, "_client", lambda: client)
    monkeypatch.setattr(scheduler, "_client", lambda: client)
    return client


def _ready_preflight() -> queue.PreflightInput:
    return {
        "active_project": True,
        "readiness": True,
        "issue_context": True,
        "credentials": True,
        "ai_hub_ready": True,
        "sandbox_profile": True,
        "budget": True,
        "duplicate_active_run": False,
        "kill_switch": False,
    }


async def test_upsert_dedupes_by_project_provider_and_external_id(fake_client: _FakeClient) -> None:
    first = await queue.upsert_delivery_queue_item(
        {
            "project_id": "project-1",
            "provider": "jira",
            "external_work_item_id": "ABC-123",
            "title": "First title",
        },
        preflight=_ready_preflight(),
    )

    second = await queue.upsert_delivery_queue_item(
        {
            "project_id": "project-1",
            "provider": "jira",
            "external_work_item_id": "ABC-123",
            "title": "Updated title",
        },
        preflight=_ready_preflight(),
    )

    records = await queue.list_delivery_queue_items({"project_id": "project-1"})
    assert len(records) == 1
    assert first["id"] == second["id"]
    assert second["dedupe_key"] == "project-1:jira:ABC-123"
    assert second["title"] == "Updated title"
    assert second["status"] == "queued"
    assert len(fake_client.store.items) == 1


async def test_readiness_removal_pauses_active_item_and_marks_new_item_not_ready(
    fake_client: _FakeClient,
) -> None:
    await queue.upsert_delivery_queue_item(
        {
            "project_id": "project-1",
            "provider": "jira",
            "external_work_item_id": "ABC-123",
        },
        preflight=_ready_preflight(),
    )
    paused = await queue.upsert_delivery_queue_item(
        {
            "project_id": "project-1",
            "provider": "jira",
            "external_work_item_id": "ABC-123",
        },
        preflight={**_ready_preflight(), "readiness": False},
    )

    not_ready = await queue.upsert_delivery_queue_item(
        {
            "project_id": "project-1",
            "provider": "github",
            "external_work_item_id": "99",
        },
        preflight={**_ready_preflight(), "readiness": False},
    )

    assert paused["status"] == "paused"
    assert paused["blockers"] == [
        {"code": "readiness", "message": "Work item is not ready for delivery."}
    ]
    assert not_ready["status"] == "not-ready"


def test_start_preflight_aggregates_structured_blockers() -> None:
    result = queue.evaluate_start_preflight(
        active_project=False,
        readiness=False,
        issue_context=False,
        credentials=False,
        ai_hub_ready=False,
        sandbox_profile=False,
        budget=False,
        duplicate_active_run=True,
        kill_switch=True,
    )

    assert result["ready"] is False
    assert [blocker["code"] for blocker in result["blockers"]] == [
        "active_project",
        "readiness",
        "issue_context",
        "credentials",
        "ai_hub_ready",
        "sandbox_profile",
        "budget",
        "duplicate_active_run",
        "kill_switch",
    ]


def test_start_preflight_allows_valid_item() -> None:
    result = queue.evaluate_start_preflight(**_ready_preflight())

    assert result == {"ready": True, "blockers": []}


async def test_status_transition_updates_record(fake_client: _FakeClient) -> None:
    record = await queue.upsert_delivery_queue_item(
        {
            "project_id": "project-1",
            "provider": "jira",
            "external_work_item_id": "ABC-123",
        },
        preflight=_ready_preflight(),
    )

    updated = await queue.transition_delivery_queue_status(
        record["id"],
        "running",
        reason="worker claimed item",
    )

    assert updated["status"] == "running"
    assert updated["status_reason"] == "worker claimed item"
    assert updated["previous_status"] == "queued"
    assert await queue.read_delivery_queue_item(record["id"]) == updated


async def test_scheduler_dispatches_delivery_queue_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    poll = AsyncMock(return_value={"status": "polled", "items": 2})
    monkeypatch.setattr(scheduler, "delivery_queue_poll", poll)

    result = await scheduler._launch({"task": "delivery_queue_poll"}, {"configurable": {}})

    assert result == {"result": {"status": "polled", "items": 2}}
    poll.assert_awaited_once_with()


async def test_ensure_delivery_queue_polling_cron_registers_five_minute_task(
    fake_client: _FakeClient,
) -> None:
    record = await scheduler.ensure_delivery_queue_polling_cron()

    assert record["cron_id"] == "cron_1"
    assert record["schedule"] == "*/5 * * * *"
    assert len(fake_client.crons.created) == 1
    created = fake_client.crons.created[0]
    assert created["assistant_id"] == "scheduler"
    assert created["schedule"] == "*/5 * * * *"
    assert created["input"] == {"task": "delivery_queue_poll"}
    assert created["config"]["configurable"]["task"] == "delivery_queue_poll"
    assert created["metadata"]["kind"] == "delivery_queue_poll"


async def test_ensure_delivery_queue_polling_cron_is_idempotent(
    fake_client: _FakeClient,
) -> None:
    await scheduler.ensure_delivery_queue_polling_cron()
    second = await scheduler.ensure_delivery_queue_polling_cron()

    assert second["cron_id"] == "cron_1"
    assert len(fake_client.crons.created) == 1
