from __future__ import annotations

from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import delivery_queue as queue
from agent import linear_queue
from agent import delivery_runner as runner
from agent import project_registry, project_secrets
from agent.dashboard import oauth, provider_pat_vault, routes

_TEST_SECRET = "test-secret-with-at-least-thirty-two-bytes"


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: list[str], key: str) -> dict[str, Any] | None:
        value = self.items.get((tuple(namespace), key))
        return {"value": value} if value is not None else None

    async def put_item(self, namespace: list[str], key: str, value: dict[str, Any]) -> None:
        self.items[(tuple(namespace), key)] = value

    async def delete_item(self, namespace: list[str], key: str) -> None:
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


class _FakeThreads:
    async def create(
        self,
        *,
        thread_id: str,
        metadata: dict[str, Any],
        if_exists: str = "raise",
    ) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": metadata}

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        return None

    async def get(self, thread_id: str) -> dict[str, Any]:
        return {"thread_id": thread_id, "status": "idle", "metadata": {}}


class _FakeRuns:
    async def list(
        self,
        thread_id: str,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return []


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()
        self.threads = _FakeThreads()
        self.runs = _FakeRuns()


class _DispatchRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        thread_id: str,
        content: str,
        configurable: dict[str, Any],
        *,
        source: str,
        assistant_id: str = "agent",
        metadata: dict[str, Any] | None = None,
        client: Any = None,
    ) -> dict[str, Any]:
        self.calls.append({"thread_id": thread_id, "configurable": configurable})
        return {"run_id": "run-worker-1"}


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", _TEST_SECRET)
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://testserver")
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "http://testserver")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    client = _FakeClient()
    monkeypatch.setattr(project_secrets, "_client", lambda: client)
    monkeypatch.setattr(project_registry, "_client", lambda: client)
    monkeypatch.setattr(provider_pat_vault, "_client", lambda: client)
    monkeypatch.setattr(queue, "_client", lambda: client)
    return client


@pytest.fixture
def dispatch_recorder(monkeypatch: pytest.MonkeyPatch) -> _DispatchRecorder:
    recorder = _DispatchRecorder()
    monkeypatch.setattr(runner, "dispatch_agent_run", recorder)
    return recorder


@pytest.fixture
def dashboard_client(fake_client: _FakeClient) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def _session_cookie(login: str = "octocat", email: str = "octo@example.com") -> dict[str, str]:
    token = oauth.issue_session(login=login, email=email, avatar_url=None)
    return {oauth.COOKIE_NAME: token}


async def _project(project_id: str = "sports-cms", *, users: list[str] | None = None) -> None:
    await project_registry.upsert_delivery_project(
        project_registry.default_delivery_project(
            project_id=project_id,
            name=project_id,
            tracker_config={"project_id": f"linear-{project_id}"},
            vcs_config={"owner": "example", "repo": project_id},
            membership={"users": users or ["octocat"]},
        )
    )


async def test_delivery_project_list_is_scoped_to_project_members(
    dashboard_client: TestClient,
) -> None:
    await _project("sports-cms", users=["octocat"])
    await _project("solcom", users=["hubot"])

    response = dashboard_client.get(
        "/dashboard/api/delivery-projects",
        headers={"Origin": "http://testserver"},
        cookies=_session_cookie(login="octocat", email="octo@example.com"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["project_id"] for item in payload["items"]] == ["sports-cms"]
    project = payload["items"][0]
    assert project["tracker"]["provider"] == "linear"
    assert project["vcs"]["provider"] == "github"
    assert project["member_logins"] == ["octocat"]


async def test_admin_delivery_project_list_includes_all_projects(
    dashboard_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin@example.com")
    await _project("sports-cms", users=["octocat"])
    await _project("solcom", users=["hubot"])

    response = dashboard_client.get(
        "/dashboard/api/delivery-projects",
        headers={"Origin": "http://testserver"},
        cookies=_session_cookie(login="admin", email="admin@example.com"),
    )

    assert response.status_code == 200
    assert sorted(item["project_id"] for item in response.json()["items"]) == [
        "solcom",
        "sports-cms",
    ]


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


async def _queued_sports_item() -> dict[str, Any]:
    return await queue.upsert_delivery_queue_item(
        {
            "project_id": "sports-cms",
            "provider": "linear",
            "external_work_item_id": "SPORT-123",
            "title": "Fix Sports CMS teaser",
            "description": "The teaser CTA is broken on mobile.",
            "github_login": "octocat",
            "credential_identity": "github:user:octocat",
            "model_snapshot": "ai-hub:model-1",
            "repo": {"owner": "example", "name": "sports-cms"},
            "sandbox_profile": {"provider": "langsmith", "profile": "sports-cms"},
        },
        preflight=_ready_preflight(),
    )


async def test_delivery_project_list_includes_latest_runs(
    dashboard_client: TestClient,
) -> None:
    await _project("sports-cms", users=["octocat"])
    await _queued_sports_item()

    response = dashboard_client.get(
        "/dashboard/api/delivery-projects",
        headers={"Origin": "http://testserver"},
        cookies=_session_cookie(login="octocat", email="octo@example.com"),
    )

    assert response.status_code == 200
    project = response.json()["items"][0]
    assert project["latest_runs"] == [
        {
            "id": "sports-cms:linear:SPORT-123",
            "status": "queued",
            "title": "Fix Sports CMS teaser",
            "provider": "linear",
            "external_work_item_id": "SPORT-123",
            "thread_id": None,
            "pull_request_url": None,
            "updated_at": project["latest_runs"][0]["updated_at"],
        }
    ]


async def _ready_sports_project(users: list[str] | None = None) -> None:
    await project_registry.upsert_delivery_project(
        project_registry.default_sports_cms_delivery_project(
            tracker_config={"project_ids": ["linear-sports"], "labels": ["agent-ready"]},
            vcs_config={"owner": "example", "repo": "sports-cms"},
            membership={"users": users or ["octocat"]},
        )
    )


async def _ready_credentials(login: str = "octocat") -> None:
    await provider_pat_vault.upsert_provider_pat(
        login,
        provider="github",
        token="ghp_octocat-token-1234",
    )
    await project_secrets.upsert_project_secret(
        "sports-cms",
        environment="default",
        name="AI_HUB_BASE_URL",
        value="https://ai-hub.example/v1",
        updated_by=login,
    )
    await project_secrets.upsert_project_secret(
        "sports-cms",
        environment="default",
        name="AI_HUB_API_KEY",
        value="valid-key",
        updated_by=login,
    )


def _check(payload: dict[str, Any], key: str) -> dict[str, Any]:
    for check in payload["checks"]:
        if check["key"] == key:
            return check
    raise AssertionError(f"missing readiness check: {key}")


async def test_delivery_project_readiness_reports_ready_workspace(
    dashboard_client: TestClient,
) -> None:
    await _ready_sports_project()
    await _ready_credentials()

    response = dashboard_client.get(
        "/dashboard/api/delivery-projects/sports-cms/readiness",
        headers={"Origin": "http://testserver"},
        cookies=_session_cookie(login="octocat", email="octo@example.com"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is True
    assert {check["key"]: check["ready"] for check in payload["checks"]} == {
        "tracker_intake": True,
        "repository_access": True,
        "user_provider_token": True,
        "project_secrets": True,
        "ai_hub": True,
        "sandbox_profile": True,
        "model_routing": True,
        "queue_policy": True,
        "auto_mode_limits": True,
        "qa_gates": True,
        "merge_policy": True,
    }


async def test_delivery_project_readiness_reports_missing_linear_config(
    dashboard_client: TestClient,
) -> None:
    await _ready_sports_project()
    await _ready_credentials()
    project = await project_registry.get_delivery_project("sports-cms")
    assert project is not None
    project["tracker"]["config"] = {}
    await project_registry.upsert_delivery_project(project)

    response = dashboard_client.get(
        "/dashboard/api/delivery-projects/sports-cms/readiness",
        headers={"Origin": "http://testserver"},
        cookies=_session_cookie(login="octocat", email="octo@example.com"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is False
    assert _check(payload, "tracker_intake")["ready"] is False
    assert _check(payload, "tracker_intake")["section"] == "ticket-intake"


def test_dashboard_ticket_intake_routes_persist_config_and_report_missing_credentials(
    dashboard_client: TestClient,
) -> None:
    import anyio

    anyio.run(_project)

    missing = dashboard_client.post(
        "/dashboard/api/delivery-projects/sports-cms/ticket-intake/test-connection",
        headers={"Origin": "http://testserver"},
        cookies=_session_cookie(),
    )
    assert missing.status_code == 200
    assert missing.json()["status"] == "missing_credentials"
    assert "LINEAR_API_KEY" in missing.json()["error"]

    saved = dashboard_client.put(
        "/dashboard/api/delivery-projects/sports-cms/ticket-intake",
        headers={"Origin": "http://testserver"},
        cookies=_session_cookie(),
        json={
            "provider": "linear",
            "team_keys": ["SPORT"],
            "linear_project_ids": ["linear-sports"],
            "labels": ["agent-ready"],
            "ready_states": ["ready"],
            "excluded_statuses": ["done", "completed"],
            "required_fields": ["description"],
            "missing_readiness": "not-ready",
            "polling_interval_minutes": 5,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["tracker_config"]["team_keys"] == ["SPORT"]
    assert saved.json()["tracker_config"]["linear_project_ids"] == ["linear-sports"]
    assert saved.json()["queue_eligibility_policy"]["labels"] == ["agent-ready"]

    project = anyio.run(project_registry.get_delivery_project, "sports-cms")
    assert project["tracker"] == {
        "provider": "linear",
        "config": {
            "team_ids": [],
            "team_keys": ["SPORT"],
            "team_names": [],
            "linear_project_ids": ["linear-sports"],
            "linear_project_names": [],
        },
    }
    assert project["queue_eligibility_policy"]["missing_readiness"] == "not-ready"


def test_dashboard_ticket_intake_test_and_preview_are_read_only(
    dashboard_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import anyio
    from unittest.mock import AsyncMock

    anyio.run(_project)
    monkeypatch.setenv("LINEAR_API_KEY", "linear-test-key")
    monkeypatch.setattr(
        linear_queue,
        "test_linear_connection",
        AsyncMock(
            return_value={
                "status": "connected",
                "provider": "linear",
                "teams": [{"id": "team-1", "key": "SPORT", "name": "Sports"}],
                "projects": [{"id": "linear-sports", "name": "Sports CMS"}],
            }
        ),
    )
    monkeypatch.setattr(
        linear_queue,
        "preview_linear_delivery_queue",
        AsyncMock(
            return_value={
                "status": "previewed",
                "provider": "linear",
                "counts": {"queued": 1, "not-ready": 1, "blocked": 0, "ignored": 0},
                "items": [
                    {"action": "queued", "identifier": "SPORT-1", "title": "Ready item"},
                    {"action": "not-ready", "identifier": "SPORT-2", "title": "Missing label"},
                ],
            }
        ),
    )

    connected = dashboard_client.post(
        "/dashboard/api/delivery-projects/sports-cms/ticket-intake/test-connection",
        headers={"Origin": "http://testserver"},
        cookies=_session_cookie(),
    )
    preview = dashboard_client.post(
        "/dashboard/api/delivery-projects/sports-cms/ticket-intake/preview",
        headers={"Origin": "http://testserver"},
        cookies=_session_cookie(),
    )

    assert connected.status_code == 200
    assert connected.json()["teams"][0]["key"] == "SPORT"
    assert preview.status_code == 200
    assert preview.json()["counts"] == {
        "queued": 1,
        "not-ready": 1,
        "blocked": 0,
        "ignored": 0,
    }
    assert anyio.run(queue.list_delivery_queue_items) == []


async def test_delivery_project_readiness_reports_missing_repo(
    dashboard_client: TestClient,
) -> None:
    await _ready_sports_project()
    await _ready_credentials()
    project = await project_registry.get_delivery_project("sports-cms")
    assert project is not None
    project["vcs"]["config"] = {"owner": "example"}
    await project_registry.upsert_delivery_project(project)

    response = dashboard_client.get(
        "/dashboard/api/delivery-projects/sports-cms/readiness",
        headers={"Origin": "http://testserver"},
        cookies=_session_cookie(login="octocat", email="octo@example.com"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is False
    assert _check(payload, "repository_access")["ready"] is False
    assert _check(payload, "repository_access")["section"] == "repositories"


async def test_delivery_project_readiness_reports_missing_user_pat(
    dashboard_client: TestClient,
) -> None:
    await _ready_sports_project()
    await project_secrets.upsert_project_secret(
        "sports-cms",
        environment="default",
        name="AI_HUB_BASE_URL",
        value="https://ai-hub.example/v1",
        updated_by="octocat",
    )
    await project_secrets.upsert_project_secret(
        "sports-cms",
        environment="default",
        name="AI_HUB_API_KEY",
        value="valid-key",
        updated_by="octocat",
    )

    response = dashboard_client.get(
        "/dashboard/api/delivery-projects/sports-cms/readiness",
        headers={"Origin": "http://testserver"},
        cookies=_session_cookie(login="octocat", email="octo@example.com"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is False
    assert _check(payload, "user_provider_token")["ready"] is False
    assert _check(payload, "user_provider_token")["section"] == "credentials"


async def test_delivery_project_readiness_reports_missing_ai_hub_secret(
    dashboard_client: TestClient,
) -> None:
    await _ready_sports_project()
    await provider_pat_vault.upsert_provider_pat(
        "octocat",
        provider="github",
        token="ghp_octocat-token-1234",
    )
    await project_secrets.upsert_project_secret(
        "sports-cms",
        environment="default",
        name="AI_HUB_BASE_URL",
        value="https://ai-hub.example/v1",
        updated_by="octocat",
    )

    response = dashboard_client.get(
        "/dashboard/api/delivery-projects/sports-cms/readiness",
        headers={"Origin": "http://testserver"},
        cookies=_session_cookie(login="octocat", email="octo@example.com"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is False
    assert _check(payload, "project_secrets")["ready"] is False
    assert _check(payload, "ai_hub")["ready"] is False
    assert _check(payload, "ai_hub")["blockers"] == [
        {"code": "missing_ai_hub_api_key", "message": "AI Hub API key is missing."}
    ]


async def test_delivery_project_readiness_reports_disabled_auto_mode(
    dashboard_client: TestClient,
) -> None:
    await _ready_sports_project()
    await _ready_credentials()
    project = await project_registry.get_delivery_project("sports-cms")
    assert project is not None
    project["kill_switch"] = True
    await project_registry.upsert_delivery_project(project)

    response = dashboard_client.get(
        "/dashboard/api/delivery-projects/sports-cms/readiness",
        headers={"Origin": "http://testserver"},
        cookies=_session_cookie(login="octocat", email="octo@example.com"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is False
    assert _check(payload, "auto_mode_limits")["ready"] is False
    assert _check(payload, "auto_mode_limits")["section"] == "policies"


async def test_project_secrets_are_encrypted_and_scoped_by_project_environment(
    fake_client: _FakeClient,
) -> None:
    first = await project_secrets.upsert_project_secret(
        "sports-cms",
        environment="prod",
        name="AI_HUB_API_KEY",
        value="prod-secret-1234",
        updated_by="octocat",
    )
    await project_secrets.upsert_project_secret(
        "sports-cms",
        environment="stage",
        name="AI_HUB_API_KEY",
        value="stage-secret-5555",
        updated_by="octocat",
    )
    await project_secrets.upsert_project_secret(
        "other-project",
        environment="prod",
        name="AI_HUB_API_KEY",
        value="other-secret-9999",
        updated_by="hubot",
    )

    assert first["project_id"] == "sports-cms"
    assert first["environment"] == "prod"
    assert first["name"] == "AI_HUB_API_KEY"
    assert first["value_last4"] == "1234"
    assert "value" not in first

    stored = fake_client.store.items[
        (("delivery_project_secrets", "sports-cms", "prod"), "AI_HUB_API_KEY")
    ]
    assert stored["encrypted_value"] != "prod-secret-1234"
    assert "prod-secret-1234" not in str(stored)

    resolved = await project_secrets.resolve_project_secret(
        "sports-cms",
        environment="prod",
        name="AI_HUB_API_KEY",
    )
    assert resolved == "prod-secret-1234"
    assert (
        await project_secrets.resolve_project_secret(
            "sports-cms",
            environment="stage",
            name="AI_HUB_API_KEY",
        )
        == "stage-secret-5555"
    )
    assert (
        await project_secrets.resolve_project_secret(
            "other-project",
            environment="prod",
            name="AI_HUB_API_KEY",
        )
        == "other-secret-9999"
    )

    rotated = await project_secrets.upsert_project_secret(
        "sports-cms",
        environment="prod",
        name="AI_HUB_API_KEY",
        value="rotated-secret-0000",
        updated_by="octocat",
    )
    assert rotated["version"] == 2
    assert rotated["value_last4"] == "0000"

    revoked = await project_secrets.revoke_project_secret(
        "sports-cms",
        environment="prod",
        name="AI_HUB_API_KEY",
    )
    assert revoked == {
        "connected": False,
        "project_id": "sports-cms",
        "environment": "prod",
        "name": "AI_HUB_API_KEY",
    }


def test_ai_hub_shape_import_reports_presence_without_values() -> None:
    shape = project_secrets.import_ai_hub_shape_from_env(
        {
            "CUSTOM_AI_HUB_BASE_URL": "https://ai-hub.example/v1",
            "CUSTOM_AI_HUB_API_KEY": "secret-key",
            "CUSTOM_AI_HUB_MODELS": "model-a,model-b",
        },
        prefixes=("CUSTOM_AI_HUB",),
    )

    assert shape == {
        "provider": "ai_hub",
        "candidates": [
            {
                "prefix": "CUSTOM_AI_HUB",
                "required_secrets": [
                    {
                        "name": "AI_HUB_BASE_URL",
                        "source_env": "CUSTOM_AI_HUB_BASE_URL",
                        "present": True,
                    },
                    {
                        "name": "AI_HUB_API_KEY",
                        "source_env": "CUSTOM_AI_HUB_API_KEY",
                        "present": True,
                    },
                ],
                "model_list_env": "CUSTOM_AI_HUB_MODELS",
                "model_list_present": True,
            }
        ],
    }
    assert "secret-key" not in str(shape)
    assert "https://ai-hub.example" not in str(shape)


async def test_ai_hub_env_import_stores_credentials_without_returning_values(
    fake_client: _FakeClient,
) -> None:
    imported = await project_secrets.import_ai_hub_secrets_from_env(
        "sports-cms",
        environment="prod",
        updated_by="octocat",
        env={
            "CUSTOM_AI_HUB_BASE_URL": "https://ai-hub.example/v1",
            "CUSTOM_AI_HUB_API_KEY": "secret-key-1234",
        },
        prefixes=("CUSTOM_AI_HUB",),
    )

    assert imported["source_prefix"] == "CUSTOM_AI_HUB"
    assert [secret["name"] for secret in imported["imported"]] == [
        "AI_HUB_BASE_URL",
        "AI_HUB_API_KEY",
    ]
    assert "secret-key-1234" not in str(imported)
    assert "https://ai-hub.example" not in str(imported)
    assert (
        await project_secrets.resolve_project_secret(
            "sports-cms",
            environment="prod",
            name="AI_HUB_API_KEY",
        )
        == "secret-key-1234"
    )


async def test_ai_hub_readiness_success_missing_and_invalid(
    fake_client: _FakeClient,
) -> None:
    missing = await project_secrets.evaluate_ai_hub_readiness(
        "sports-cms",
        environment="prod",
    )
    assert missing["ready"] is False
    assert [blocker["code"] for blocker in missing["blockers"]] == [
        "missing_ai_hub_base_url",
        "missing_ai_hub_api_key",
    ]

    await project_secrets.upsert_project_secret(
        "sports-cms",
        environment="prod",
        name="AI_HUB_BASE_URL",
        value="https://ai-hub.example/v1",
        updated_by="octocat",
    )
    await project_secrets.upsert_project_secret(
        "sports-cms",
        environment="prod",
        name="AI_HUB_API_KEY",
        value="valid-key",
        updated_by="octocat",
    )
    seen: dict[str, str] = {}

    def validator(credentials: project_secrets.AIHubCredentials) -> bool:
        seen["base_url"] = credentials.base_url
        seen["api_key"] = credentials.api_key
        return True

    ready = await project_secrets.evaluate_ai_hub_readiness(
        "sports-cms",
        environment="prod",
        validator=validator,
    )
    assert ready == {"ready": True, "blockers": [], "environment": "prod"}
    assert seen == {"base_url": "https://ai-hub.example/v1", "api_key": "valid-key"}
    assert "valid-key" not in str(ready)

    invalid = await project_secrets.evaluate_ai_hub_readiness(
        "sports-cms",
        environment="prod",
        validator=lambda _credentials: False,
    )
    assert invalid["ready"] is False
    assert invalid["blockers"] == [
        {"code": "invalid_ai_hub_credentials", "message": "AI Hub credentials are invalid."}
    ]


def test_dashboard_project_secret_routes_require_membership_and_redact(
    dashboard_client: TestClient,
    fake_client: _FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import anyio

    anyio.run(_project)

    forbidden = dashboard_client.put(
        "/dashboard/api/delivery-projects/sports-cms/secrets/AI_HUB_API_KEY",
        headers={"Origin": "http://testserver"},
        cookies=_session_cookie(login="hubot", email="hubot@example.com"),
        json={"environment": "prod", "value": "secret-route-1234"},
    )
    assert forbidden.status_code == 403

    created = dashboard_client.put(
        "/dashboard/api/delivery-projects/sports-cms/secrets/AI_HUB_API_KEY",
        headers={"Origin": "http://testserver"},
        cookies=_session_cookie(),
        json={"environment": "prod", "value": "secret-route-1234"},
    )
    assert created.status_code == 200
    assert created.json()["value_last4"] == "1234"
    assert "secret-route-1234" not in str(created.json())

    listed = dashboard_client.get(
        "/dashboard/api/delivery-projects/sports-cms/secrets",
        cookies=_session_cookie(),
        params={"environment": "prod"},
    )
    assert listed.status_code == 200
    assert listed.json()["items"][0]["name"] == "AI_HUB_API_KEY"
    assert "secret-route-1234" not in str(listed.json())

    tested = dashboard_client.post(
        "/dashboard/api/delivery-projects/sports-cms/secrets/AI_HUB_API_KEY/test",
        headers={"Origin": "http://testserver"},
        cookies=_session_cookie(),
        json={"environment": "prod"},
    )
    assert tested.status_code == 200
    assert tested.json()["ready"] is True
    assert "secret-route-1234" not in str(tested.json())

    monkeypatch.setenv("CUSTOM_AI_HUB_BASE_URL", "https://ai-hub.example/v1")
    monkeypatch.setenv("CUSTOM_AI_HUB_API_KEY", "route-import-secret-4444")
    imported = dashboard_client.post(
        "/dashboard/api/delivery-projects/sports-cms/ai-hub/import",
        headers={"Origin": "http://testserver"},
        cookies=_session_cookie(),
        json={"environment": "prod", "prefixes": ["CUSTOM_AI_HUB"]},
    )
    assert imported.status_code == 200
    assert imported.json()["source_prefix"] == "CUSTOM_AI_HUB"
    assert "route-import-secret-4444" not in str(imported.json())

    deleted = dashboard_client.delete(
        "/dashboard/api/delivery-projects/sports-cms/secrets/AI_HUB_API_KEY",
        headers={"Origin": "http://testserver"},
        cookies=_session_cookie(),
        params={"environment": "prod"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["connected"] is False


async def test_delivery_worker_blocks_when_project_ai_hub_is_not_ready(
    fake_client: _FakeClient,
    dispatch_recorder: _DispatchRecorder,
) -> None:
    await provider_pat_vault.upsert_provider_pat(
        "octocat",
        provider="github",
        token="ghp_octocat-token-1234",
    )
    await project_registry.upsert_delivery_project(
        project_registry.default_sports_cms_delivery_project(
            tracker_config={"project_ids": ["linear-sports"]},
            vcs_config={"owner": "example", "repo": "sports-cms"},
            membership={"users": ["octocat"]},
        )
    )
    record = await _queued_sports_item()

    blocked = await runner.launch_delivery_worker(record["id"], client=fake_client)

    assert blocked["status"] == "refused"
    assert blocked["reason"] == "ai_hub_ready"
    assert dispatch_recorder.calls == []

    await project_secrets.upsert_project_secret(
        "sports-cms",
        environment="default",
        name="AI_HUB_BASE_URL",
        value="https://ai-hub.example/v1",
        updated_by="octocat",
    )
    await project_secrets.upsert_project_secret(
        "sports-cms",
        environment="default",
        name="AI_HUB_API_KEY",
        value="valid-key",
        updated_by="octocat",
    )
    await queue.transition_delivery_queue_status(
        record["id"],
        "queued",
        reason="retry",
        extra={"preflight": {"ready": True, "blockers": []}, "blockers": []},
    )

    launched = await runner.launch_delivery_worker(record["id"], client=fake_client)

    assert launched["status"] == "launched"
    assert len(dispatch_recorder.calls) == 1
