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
    assert beta["queue_eligibility_policy"] == {
        "ready_states": ["ready"],
        "labels": ["agent-ready"],
    }

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


async def test_branch_policy_storage_and_lookup(fake_client: _FakeClient) -> None:
    project = _project("alpha", "Alpha")
    project["branch_policy"] = {
        "base_branch": "develop",
        "branch_prefix": "delivery/alpha",
        "draft_pull_requests": True,
    }
    await project_registry.upsert_delivery_project(project)

    branch_policy = await project_registry.get_project_branch_policy("alpha")

    assert branch_policy == {
        "base_branch": "develop",
        "branch_prefix": "delivery/alpha",
        "draft_pull_requests": True,
    }


async def test_worker_context_policy_storage(fake_client: _FakeClient) -> None:
    project = _project("alpha", "Alpha")
    project["context_pack"] = {"documents": ["README.md"], "repositories": ["alpha"]}
    project["credential_policy"] = {
        "provider": "github",
        "scope": "user",
        "requires_user_pat": True,
    }

    stored = await project_registry.upsert_delivery_project(project)

    assert stored["context_pack"] == {"documents": ["README.md"], "repositories": ["alpha"]}
    assert stored["credential_policy"] == {
        "provider": "github",
        "scope": "user",
        "requires_user_pat": True,
    }


def test_default_sports_cms_project_profile_is_ready_for_v1_configuration() -> None:
    project = project_registry.default_sports_cms_delivery_project(
        tracker_config={"team_keys": ["ENG"], "linear_project_ids": ["project-linear-1"]},
        vcs_config={"owner": "example", "repo": "sports-cms"},
    )

    assert project["project_id"] == "sports-cms"
    assert project["tracker"] == {
        "provider": "linear",
        "config": {"team_keys": ["ENG"], "linear_project_ids": ["project-linear-1"]},
    }
    assert project["vcs"] == {
        "provider": "github",
        "config": {"owner": "example", "repo": "sports-cms"},
    }
    assert project["queue_eligibility_policy"]["labels"] == ["agent-ready"]
    assert project["queue_eligibility_policy"]["missing_readiness"] == "not-ready"
    assert project["branch_policy"]["base_branch"] == "main"
    assert project["branch_policy"]["branch_prefix"] == "delivery/sports-cms"
    assert project["sandbox_profile"] == {"provider": "langsmith", "profile": "sports-cms"}
    assert project["context_pack"] == {
        "domains": ["drupal", "sdc", "frontend"],
        "required_context": ["project_readme", "theme_components", "qa_gates"],
    }
    assert project["credential_policy"] == {
        "provider": "github",
        "scope": "user",
        "requires_user_pat": True,
        "allowed_actions": ["branch", "commit", "pull_request"],
    }
    assert project["membership"] == {"users": []}


def test_default_sports_cms_project_accepts_runtime_sandbox_profile() -> None:
    sandbox_profile = project_registry.sports_cms_ddev_sandbox_profile(
        project_path="/workspace/sports-cms",
        preview_url="https://sports.example.test/",
        theme_path="web/themes/custom/sports_theme",
        artifact_dir="/tmp/sports-artifacts",
    )

    project = project_registry.default_sports_cms_delivery_project(
        tracker_config={"team_keys": ["ENG"], "linear_project_ids": ["project-linear-1"]},
        vcs_config={"owner": "example", "repo": "sports-cms"},
        sandbox_profile=sandbox_profile,
    )

    assert project["sandbox_profile"]["provider"] == "ddev"
    assert project["sandbox_profile"]["runtime"]["project_path"] == "/workspace/sports-cms"
    assert project["sandbox_profile"]["runtime"]["preview_url"] == "https://sports.example.test/"


def test_sports_cms_ddev_sandbox_profile_contains_runtime_gates() -> None:
    profile = project_registry.sports_cms_ddev_sandbox_profile(
        project_path="/workspace/sports-cms",
        preview_url="https://sports.example.test/",
        theme_path="web/themes/custom/sports_theme",
        artifact_dir="/tmp/sports-artifacts",
        sdc_component_id="sports_theme:card_news",
    )

    assert profile["provider"] == "ddev"
    runtime = profile["runtime"]
    assert runtime["project_path"] == "/workspace/sports-cms"
    assert runtime["preview_url"] == "https://sports.example.test/"
    gates = runtime["gates"]
    assert [gate["name"] for gate in gates] == [
        "drupal_bootstrap",
        "theme_assets",
        "sdc_twig_render",
        "browser_flow",
        "screenshot",
        "trace_or_video",
    ]
    assert gates[1]["command"] == "test -f web/themes/custom/sports_theme/build/main.min.css"
    assert "hasDefinition('sports_theme:card_news')" in gates[2]["command"]
    assert gates[4]["artifact_path"] == "/tmp/sports-artifacts/sports-cms-home.png"
    assert gates[5]["cwd"] == "/tmp/sports-artifacts"
    assert gates[5]["artifact_path"] == "/tmp/sports-artifacts/sports-cms-trace.zip"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"project_path": ""}, "project_path is required"),
        ({"preview_url": ""}, "preview_url is required"),
        ({"theme_path": ""}, "theme_path is required"),
        ({"artifact_dir": ""}, "artifact_dir is required"),
    ],
)
def test_sports_cms_ddev_sandbox_profile_requires_runtime_inputs(
    overrides: dict[str, str],
    message: str,
) -> None:
    payload = {
        "project_path": "/workspace/sports-cms",
        "preview_url": "https://sports.example.test/",
        "theme_path": "web/themes/custom/sports_theme",
        "artifact_dir": "/tmp/sports-artifacts",
        **overrides,
    }

    with pytest.raises(ValueError, match=message):
        project_registry.sports_cms_ddev_sandbox_profile(**payload)


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
