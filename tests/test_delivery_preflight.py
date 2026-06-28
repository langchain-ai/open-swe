from __future__ import annotations

from typing import Any

import pytest

from agent import delivery_preflight as preflight
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
    return client


def _item(**overrides: Any) -> dict[str, Any]:
    item = {
        "id": "sports-cms:linear:lin-1",
        "status": "queued",
        "description": "Implement a small SDC fix.",
        "credential_identity": "github:user:octocat",
        "model_snapshot": "openai:gpt-5",
        "sandbox_profile": {"provider": "langsmith", "profile": "sports-cms"},
        "preflight": {"ready": True, "blockers": []},
    }
    item.update(overrides)
    return item


def _project(**overrides: Any) -> dict[str, Any]:
    project = {
        "project_id": "sports-cms",
        "active": True,
        "kill_switch": False,
        "sandbox_profile": {"provider": "langsmith", "profile": "sports-cms"},
        "run_limits": {
            "max_concurrent_auto_runs": 1,
            "max_auto_startable_items": 5,
            "max_run_retries": 1,
            "max_gate_retries": 1,
            "max_auto_rework_passes": 1,
            "daily_run_budget": 10,
        },
    }
    project.update(overrides)
    return project


@pytest.mark.parametrize(
    ("item_patch", "project_patch", "checks", "code"),
    [
        ({}, {"active": False}, {}, "active_project"),
        ({"status": "not-ready"}, {}, {}, "readiness"),
        ({"description": ""}, {}, {}, "issue_context"),
        ({"credential_identity": "", "github_login": ""}, {}, {}, "credentials"),
        ({"model_snapshot": None}, {}, {}, "ai_hub_ready"),
        ({"sandbox_profile": None}, {"sandbox_profile": None}, {}, "sandbox_profile"),
        ({}, {}, {"budget_available": False}, "budget"),
        ({}, {}, {"duplicate_active_run": True}, "duplicate_active_run"),
        ({}, {"kill_switch": True}, {}, "kill_switch"),
    ],
)
def test_start_preflight_reports_each_blocker(
    item_patch: dict[str, Any],
    project_patch: dict[str, Any],
    checks: dict[str, Any],
    code: str,
) -> None:
    result = preflight.evaluate_delivery_start_preflight(
        _item(**item_patch),
        _project(**project_patch),
        checks=checks,
    )

    assert result["ready"] is False
    assert code in [blocker["code"] for blocker in result["blockers"]]


@pytest.mark.parametrize(
    ("context", "code"),
    [
        ({"active_auto_runs": 1}, "auto_active_run_limit"),
        ({"auto_startable_items": 5}, "auto_start_queue_limit"),
        ({"run_retries": 1}, "run_retry_limit"),
        ({"gate_retries": 1}, "gate_retry_limit"),
        ({"auto_rework_passes": 1}, "auto_rework_limit"),
        ({"daily_budget_remaining": 0}, "daily_budget"),
    ],
)
def test_auto_mode_limits_report_each_blocker(context: dict[str, Any], code: str) -> None:
    result = preflight.evaluate_auto_mode_limits(_project(), **context)

    assert result["ready"] is False
    assert result["blockers"] == [
        {"code": code, "message": preflight.AUTO_MODE_BLOCKER_MESSAGES[code]}
    ]


async def test_block_delivery_start_moves_queue_item_to_blocked(fake_client: _FakeClient) -> None:
    record = await queue.upsert_delivery_queue_item(
        {
            "project_id": "sports-cms",
            "provider": "linear",
            "external_work_item_id": "lin-1",
            "title": "Implement SDC fix",
        }
    )
    result = {
        "ready": False,
        "blockers": [{"code": "budget", "message": "Delivery budget is unavailable."}],
    }

    blocked = await preflight.block_delivery_start(record["id"], result)

    assert blocked["status"] == "blocked"
    assert blocked["status_reason"] == "start_preflight_failed"
    assert blocked["blockers"] == result["blockers"]
    assert blocked["preflight"] == result
