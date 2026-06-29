from __future__ import annotations

from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import delivery_queue as queue
from agent import delivery_runner as runner
from agent import project_registry, project_secrets
from agent.dashboard import oauth, provider_pat_vault, repo_access, routes

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
        self.calls.append(
            {
                "thread_id": thread_id,
                "content": content,
                "configurable": configurable,
                "source": source,
                "assistant_id": assistant_id,
                "metadata": metadata,
                "client": client,
            }
        )
        return {"run_id": "run-worker-1"}


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", _TEST_SECRET)
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://testserver")
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "http://testserver")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    client = _FakeClient()
    monkeypatch.setattr(provider_pat_vault, "_client", lambda: client)
    monkeypatch.setattr(queue, "_client", lambda: client)
    monkeypatch.setattr(project_registry, "_client", lambda: client)
    monkeypatch.setattr(project_secrets, "_client", lambda: client)
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


async def _queued_item(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "project_id": "sports-cms",
        "provider": "linear",
        "external_work_item_id": "SPORT-123",
        "title": "Fix Sports CMS teaser",
        "description": "The teaser CTA is broken on mobile.",
        "github_login": "octocat",
        "credential_identity": "github:user:octocat",
        "model_snapshot": "openai:gpt-5",
        "repo": {"owner": "example", "name": "sports-cms"},
        "sandbox_profile": {"provider": "langsmith", "profile": "sports-cms"},
    }
    payload.update(overrides)
    return await queue.upsert_delivery_queue_item(payload, preflight=_ready_preflight())


async def _stored_project() -> dict[str, Any]:
    project = project_registry.default_sports_cms_delivery_project(
        tracker_config={"project_ids": ["linear-sports"]},
        vcs_config={"owner": "example", "repo": "sports-cms"},
    )
    project["ai_hub_policy"] = {"enabled": False, "environment": "default"}
    return await project_registry.upsert_delivery_project(project)


async def test_user_can_create_update_revoke_and_inspect_redacted_pat(
    fake_client: _FakeClient,
) -> None:
    created = await provider_pat_vault.upsert_provider_pat(
        "OctoCat",
        provider="GitHub",
        token="ghp_first-secret-token-1234",
    )

    assert created["login"] == "octocat"
    assert created["provider"] == "github"
    assert created["connected"] is True
    assert created["token_last4"] == "1234"
    assert "token" not in created

    stored = fake_client.store.items[(("provider_pat_vault", "octocat"), "github")]
    assert stored["encrypted_token"] != "ghp_first-secret-token-1234"
    assert "ghp_first-secret-token-1234" not in str(stored)

    resolved = await provider_pat_vault.resolve_provider_pat(
        "octocat",
        provider="github",
        project_id="sports-cms",
        action="preflight",
    )
    assert resolved is not None
    assert resolved.token == "ghp_first-secret-token-1234"

    updated = await provider_pat_vault.upsert_provider_pat(
        "octocat",
        provider="github",
        token="ghp_second-secret-token-9999",
    )
    assert updated["token_last4"] == "9999"
    assert (await provider_pat_vault.resolve_provider_pat("octocat", provider="github")).token == (
        "ghp_second-secret-token-9999"
    )

    revoked = await provider_pat_vault.revoke_provider_pat("octocat", provider="github")
    assert revoked == {"connected": False, "provider": "github"}
    assert await provider_pat_vault.resolve_provider_pat("octocat", provider="github") is None


async def test_pat_resolution_is_scoped_to_exact_user(
    fake_client: _FakeClient,
) -> None:
    await provider_pat_vault.upsert_provider_pat(
        "octocat",
        provider="github",
        token="ghp_octocat-token-0001",
    )

    assert await provider_pat_vault.resolve_provider_pat("hubot", provider="github") is None
    assert await provider_pat_vault.resolve_provider_pat("octocat", provider="gitlab") is None


async def test_pat_use_is_audited_without_token_value(fake_client: _FakeClient) -> None:
    await provider_pat_vault.upsert_provider_pat(
        "octocat",
        provider="github",
        token="ghp_audited-token-7777",
    )

    await provider_pat_vault.resolve_provider_pat(
        "octocat",
        provider="github",
        project_id="sports-cms",
        action="branch",
    )

    audit = await provider_pat_vault.list_provider_pat_audit("octocat")
    assert audit == [
        {
            "login": "octocat",
            "project_id": "sports-cms",
            "provider": "github",
            "action": "branch",
            "status": "resolved",
            "token_last4": "7777",
            "created_at": audit[0]["created_at"],
        }
    ]
    assert "ghp_audited-token-7777" not in str(audit)


async def test_repository_access_falls_back_to_user_provider_pat(
    fake_client: _FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}
    await provider_pat_vault.upsert_provider_pat(
        "octocat",
        provider="github",
        token="ghp_repo-access-token-4321",
    )

    async def no_oauth_token(_login: str, *, force_refresh: bool = False) -> None:
        return None

    async def fake_assert_repo_access(full_name: str, token: str) -> str:
        seen["full_name"] = full_name
        seen["token"] = token
        return full_name

    monkeypatch.setattr(repo_access, "get_valid_access_token", no_oauth_token)
    monkeypatch.setattr(repo_access, "assert_repo_access", fake_assert_repo_access)

    token = await repo_access.require_repo_access_for_user(
        "octocat",
        "example/sports-cms",
    )

    assert token == "ghp_repo-access-token-4321"
    assert seen == {
        "full_name": "example/sports-cms",
        "token": "ghp_repo-access-token-4321",
    }
    audit = await provider_pat_vault.list_provider_pat_audit("octocat")
    assert audit[0]["action"] == "repository_access"
    assert audit[0]["token_last4"] == "4321"


async def test_delivery_worker_blocks_before_dispatch_when_user_pat_missing(
    fake_client: _FakeClient,
    dispatch_recorder: _DispatchRecorder,
) -> None:
    await _stored_project()
    record = await _queued_item()

    result = await runner.launch_delivery_worker(record["id"], client=fake_client)

    blocked = await queue.read_delivery_queue_item(record["id"])
    assert result["status"] == "refused"
    assert result["reason"] == "credentials"
    assert dispatch_recorder.calls == []
    assert blocked["status"] == "blocked"
    assert blocked["blockers"] == [
        {"code": "credentials", "message": "GitHub credentials are unavailable."}
    ]


async def test_delivery_worker_blocks_mismatched_credential_identity(
    fake_client: _FakeClient,
    dispatch_recorder: _DispatchRecorder,
) -> None:
    await _stored_project()
    await provider_pat_vault.upsert_provider_pat(
        "octocat",
        provider="github",
        token="ghp_octocat-token-1234",
    )
    record = await _queued_item(credential_identity="gitlab:user:octocat")

    result = await runner.launch_delivery_worker(record["id"], client=fake_client)

    assert result["status"] == "refused"
    assert result["reason"] == "credentials"
    assert dispatch_recorder.calls == []


async def test_delivery_worker_uses_only_requesting_users_pat_for_preflight(
    fake_client: _FakeClient,
    dispatch_recorder: _DispatchRecorder,
) -> None:
    await _stored_project()
    await provider_pat_vault.upsert_provider_pat(
        "octocat",
        provider="github",
        token="ghp_octocat-token-1234",
    )
    await provider_pat_vault.upsert_provider_pat(
        "other-user",
        provider="github",
        token="ghp_other-token-9999",
    )
    record = await _queued_item()

    result = await runner.launch_delivery_worker(record["id"], client=fake_client)

    updated = await queue.read_delivery_queue_item(record["id"])
    assert result["status"] == "launched"
    assert dispatch_recorder.calls
    worker_input = dispatch_recorder.calls[0]["configurable"]["delivery_worker_input"]
    assert worker_input["credential_policy"]["identity"] == "github:user:octocat"
    assert "ghp_octocat-token-1234" not in str(worker_input)
    assert "ghp_other-token-9999" not in str(worker_input)
    assert updated["credential_identity"] == "github:user:octocat"
    assert updated["credential_audit"]["login"] == "octocat"
    assert updated["credential_audit"]["provider"] == "github"
    assert updated["credential_audit"]["action"] == "preflight"


def test_dashboard_pat_routes_are_user_scoped_and_redacted(
    dashboard_client: TestClient,
    fake_client: _FakeClient,
) -> None:
    response = dashboard_client.put(
        "/dashboard/api/my-provider-tokens/github",
        headers={"Origin": "http://testserver"},
        cookies=_session_cookie(),
        json={"token": "ghp_route-secret-5555"},
    )
    assert response.status_code == 200
    assert response.json()["token_last4"] == "5555"
    assert "token" not in response.json()

    status = dashboard_client.get(
        "/dashboard/api/my-provider-tokens",
        cookies=_session_cookie(),
    )
    assert status.status_code == 200
    assert status.json()["items"][0]["provider"] == "github"
    assert "ghp_route-secret-5555" not in str(status.json())

    deleted = dashboard_client.delete(
        "/dashboard/api/my-provider-tokens/github",
        headers={"Origin": "http://testserver"},
        cookies=_session_cookie(),
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"connected": False, "provider": "github"}
