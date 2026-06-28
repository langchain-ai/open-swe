from __future__ import annotations

from typing import Any

import pytest

from agent import project_registry


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
    monkeypatch.setattr(project_registry, "_client", lambda: client)
    return client


def _project(project_id: str, name: str) -> dict[str, Any]:
    return project_registry.default_delivery_project(
        project_id=project_id,
        name=name,
        tracker_config={"team_id": f"{project_id}-tracker-team"},
        vcs_config={"owner": "example", "repo": project_id},
    )


async def test_create_update_and_list_delivery_projects(fake_client: _FakeClient) -> None:
    created = await project_registry.upsert_delivery_project(_project("alpha", "Alpha"))

    updated = await project_registry.upsert_delivery_project(
        {"project_id": "alpha", "name": "Alpha Delivery", "active": False}
    )

    records = await project_registry.list_delivery_projects()
    assert len(records) == 1
    assert created["project_id"] == updated["project_id"]
    assert updated["name"] == "Alpha Delivery"
    assert updated["active"] is False
    assert updated["created_at"] == created["created_at"]
    assert updated["updated_at"] >= created["updated_at"]
    assert await project_registry.get_delivery_project("alpha") == updated
    assert len(fake_client.store.items) == 1


async def test_projects_are_isolated_by_project_id(fake_client: _FakeClient) -> None:
    await project_registry.upsert_delivery_project(_project("alpha", "Alpha"))
    await project_registry.upsert_delivery_project(_project("beta", "Beta"))

    await project_registry.upsert_delivery_project(
        {
            "project_id": "alpha",
            "queue_eligibility_policy": {"ready_states": ["ready"], "labels": ["delivery"]},
        }
    )

    alpha = await project_registry.get_delivery_project("alpha")
    beta = await project_registry.get_delivery_project("beta")
    assert alpha is not None
    assert beta is not None
    assert alpha["queue_eligibility_policy"] == {
        "ready_states": ["ready"],
        "labels": ["delivery"],
    }
    assert beta["queue_eligibility_policy"] == {"ready_states": ["ready"], "labels": ["agent-ready"]}

    records = await project_registry.list_delivery_projects()
    assert [record["project_id"] for record in records] == ["alpha", "beta"]


def test_start_policy_blocks_disabled_project() -> None:
    project = _project("alpha", "Alpha")
    project["active"] = False

    result = project_registry.evaluate_project_start_policy(project)

    assert result["ready"] is False
    assert result["blockers"] == [{"code": "disabled_project", "message": "Project is disabled."}]


def test_start_policy_blocks_kill_switch() -> None:
    project = _project("alpha", "Alpha")
    project["kill_switch"] = True

    result = project_registry.evaluate_project_start_policy(project)

    assert result["ready"] is False
    assert result["blockers"] == [
        {"code": "kill_switch", "message": "Project kill switch is enabled."}
    ]


def test_start_policy_reports_missing_preflight_inputs() -> None:
    project = _project("alpha", "Alpha")
    project["tracker"]["config"] = {}
    project["vcs"]["config"] = {}
    project["sandbox_profile"] = None

    result = project_registry.evaluate_project_start_policy(project, budget_available=False)

    assert result["ready"] is False
    assert [blocker["code"] for blocker in result["blockers"]] == [
        "missing_tracker_config",
        "missing_vcs_config",
        "missing_sandbox",
        "missing_budget",
    ]


async def test_queue_policy_storage(fake_client: _FakeClient) -> None:
    project = _project("alpha", "Alpha")
    project["queue_eligibility_policy"] = {
        "ready_states": ["triaged", "ready"],
        "labels": ["delivery", "automated"],
        "exclude_labels": ["blocked"],
    }

    stored = await project_registry.upsert_delivery_project(project)

    assert stored["queue_eligibility_policy"] == {
        "ready_states": ["triaged", "ready"],
        "labels": ["delivery", "automated"],
        "exclude_labels": ["blocked"],
    }
    assert (await project_registry.get_delivery_project("alpha"))["queue_eligibility_policy"] == (
        stored["queue_eligibility_policy"]
    )


def test_default_membership_uses_flat_project_users() -> None:
    project = _project("alpha", "Alpha")

    assert project["membership"] == {"users": []}


async def test_merge_policy_lookup(fake_client: _FakeClient) -> None:
    project = _project("alpha", "Alpha")
    project["merge_policy"] = {
        "enabled": True,
        "strategy": "squash",
        "required_checks": ["unit", "lint"],
        "delete_branch": True,
    }
    await project_registry.upsert_delivery_project(project)

    merge_policy = await project_registry.get_project_merge_policy("alpha")

    assert merge_policy == {
        "enabled": True,
        "strategy": "squash",
        "required_checks": ["unit", "lint"],
        "delete_branch": True,
    }
