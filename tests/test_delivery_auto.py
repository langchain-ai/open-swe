from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent import delivery_auto, project_registry
from agent import delivery_queue as queue


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


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    client = _FakeClient()
    monkeypatch.setattr(queue, "_client", lambda: client)
    monkeypatch.setattr(project_registry, "_client", lambda: client)
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


async def _project(**overrides: Any) -> dict[str, Any]:
    project = project_registry.default_delivery_project(
        project_id="sports-cms",
        name="Sports CMS",
        tracker_config={"project_id": "linear-project"},
        vcs_config={"owner": "example", "repo": "sports-cms"},
        run_limits={
            "max_concurrent_auto_runs": 1,
            "max_auto_startable_items": 5,
            "daily_run_budget": 10,
        },
    )
    project.update(overrides)
    return await project_registry.upsert_delivery_project(project)


async def _queue_item(external_id: str, *, status: str = "queued") -> dict[str, Any]:
    return await queue.upsert_delivery_queue_item(
        {
            "project_id": "sports-cms",
            "provider": "linear",
            "external_work_item_id": external_id,
            "title": f"Ticket {external_id}",
            "description": "Implement a Sports CMS delivery ticket.",
            "status": status,
        },
        preflight=_ready_preflight(),
    )


async def _queue_item_for_project(
    project_id: str,
    external_id: str,
    *,
    status: str = "queued",
) -> dict[str, Any]:
    return await queue.upsert_delivery_queue_item(
        {
            "project_id": project_id,
            "provider": "linear",
            "external_work_item_id": external_id,
            "title": f"Ticket {external_id}",
            "description": "Implement a delivery ticket.",
            "status": status,
        },
        preflight=_ready_preflight(),
    )


@pytest.mark.asyncio
async def test_auto_tick_pauses_stale_repo_queue_items(
    fake_client: _FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _project(vcs={"provider": "github", "config": {"owner": "maphilipps", "repo": "adesso-sports-cms"}})
    stale = await queue.upsert_delivery_queue_item(
        {
            "project_id": "sports-cms",
            "provider": "linear",
            "external_work_item_id": "ADPHPXC-696",
            "title": "Old repo item",
            "repo": {"owner": "example", "name": "sports-cms"},
            "status": "blocked",
        },
        preflight=_ready_preflight(),
    )
    launcher = AsyncMock()
    monkeypatch.setattr(delivery_auto, "launch_delivery_worker", launcher)

    result = await delivery_auto.delivery_auto_tick(client=fake_client, poll=False)

    updated = await queue.read_delivery_queue_item(stale["id"])
    assert result["stale_reconcile"] == [{"project_id": "sports-cms", "items": 1}]
    assert updated["status"] == "paused"
    assert updated["status_reason"] == "stale_project_config"
    launcher.assert_not_called()


@pytest.mark.asyncio
async def test_auto_tick_launches_queued_delivery_item(
    fake_client: _FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _project()
    item = await _queue_item("SPORT-1")
    launcher = AsyncMock(return_value={"status": "launched", "item_id": item["id"]})
    monkeypatch.setattr(delivery_auto, "launch_delivery_worker", launcher)

    result = await delivery_auto.delivery_auto_tick(client=fake_client, poll=False)

    assert result["status"] == "completed"
    assert result["launched"] == [{"status": "launched", "item_id": item["id"]}]
    launcher.assert_awaited_once_with(
        item["id"],
        client=fake_client,
        auto_mode={"ready": True, "blockers": []},
    )


@pytest.mark.asyncio
async def test_auto_tick_skips_when_project_already_has_active_run(
    fake_client: _FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _project()
    queued = await _queue_item("SPORT-1")
    running = await _queue_item("SPORT-2")
    await queue.transition_delivery_queue_status(running["id"], "running")
    launcher = AsyncMock()
    monkeypatch.setattr(delivery_auto, "launch_delivery_worker", launcher)

    result = await delivery_auto.delivery_auto_tick(client=fake_client, poll=False)

    assert result["launched"] == []
    assert result["skipped"] == [
        {
            "item_id": queued["id"],
            "project_id": "sports-cms",
            "reason": "auto_active_run_limit",
        }
    ]
    launcher.assert_not_called()


@pytest.mark.asyncio
async def test_auto_tick_caps_auto_startable_items(
    fake_client: _FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _project(
        run_limits={
            "max_concurrent_auto_runs": 5,
            "max_auto_startable_items": 1,
            "daily_run_budget": 10,
        }
    )
    first = await _queue_item("SPORT-1")
    second = await _queue_item("SPORT-2")
    launcher = AsyncMock(return_value={"status": "launched"})
    monkeypatch.setattr(delivery_auto, "launch_delivery_worker", launcher)

    result = await delivery_auto.delivery_auto_tick(client=fake_client, poll=False)

    assert [call.args[0] for call in launcher.await_args_list] == [first["id"]]
    assert result["skipped"] == [
        {
            "item_id": second["id"],
            "project_id": "sports-cms",
            "reason": "auto_start_queue_limit",
        }
    ]


@pytest.mark.asyncio
async def test_auto_tick_can_poll_before_launching(
    fake_client: _FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _project()
    item = await _queue_item("SPORT-1")
    poll = AsyncMock(return_value={"status": "polled", "items": 1})
    launcher = AsyncMock(return_value={"status": "launched", "item_id": item["id"]})
    monkeypatch.setattr(delivery_auto, "delivery_queue_poll", poll)
    monkeypatch.setattr(delivery_auto, "launch_delivery_worker", launcher)

    result = await delivery_auto.delivery_auto_tick(client=fake_client, poll=True)

    assert result["poll"] == {"status": "polled", "items": 1}
    assert result["launched"] == [{"status": "launched", "item_id": item["id"]}]
    polled_projects = poll.await_args.kwargs["projects"]
    assert [project["project_id"] for project in polled_projects] == ["sports-cms"]


@pytest.mark.asyncio
async def test_auto_tick_can_be_scoped_to_one_project(
    fake_client: _FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _project()
    other_project = project_registry.default_delivery_project(
        project_id="other-cms",
        name="Other CMS",
        tracker_config={"project_id": "linear-other"},
        vcs_config={"owner": "example", "repo": "other-cms"},
        run_limits={
            "max_concurrent_auto_runs": 1,
            "max_auto_startable_items": 5,
            "daily_run_budget": 10,
        },
    )
    await project_registry.upsert_delivery_project(other_project)
    sports_item = await _queue_item_for_project("sports-cms", "SPORT-1")
    await _queue_item_for_project("other-cms", "OTHER-1")
    poll = AsyncMock(return_value={"status": "polled", "projects": 1, "items": 0})
    launcher = AsyncMock(return_value={"status": "launched", "item_id": sports_item["id"]})
    monkeypatch.setattr(delivery_auto, "delivery_queue_poll", poll)
    monkeypatch.setattr(delivery_auto, "launch_delivery_worker", launcher)

    result = await delivery_auto.delivery_auto_tick(
        client=fake_client,
        poll=True,
        project_id="sports-cms",
    )

    assert result["project_id"] == "sports-cms"
    assert result["queued"] == 1
    assert result["launched"] == [{"status": "launched", "item_id": sports_item["id"]}]
    assert [call.args[0] for call in launcher.await_args_list] == [sports_item["id"]]
    polled_projects = poll.await_args.kwargs["projects"]
    assert [project["project_id"] for project in polled_projects] == ["sports-cms"]
