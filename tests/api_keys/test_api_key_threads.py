from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from agent.api_keys import public_routes
from agent.api_keys.models import ApiKey
from agent.workspaces.store import WORKSPACES, WorkspaceCreate


class _FakeThreads:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> None:
        self.created.append(kwargs)

    async def update(self, **kwargs: Any) -> None:
        self.updated.append(kwargs)

    async def get(self, thread_id: str) -> dict[str, Any]:
        thread = next(
            (item for item in self.created if item["thread_id"] == thread_id),
            None,
        )
        if thread is None:
            raise LookupError(thread_id)
        return {"thread_id": thread_id, "status": "idle", "metadata": thread["metadata"]}


class _FakeRuns:
    async def list(self, thread_id: str, limit: int = 1) -> list[dict[str, Any]]:
        return [{"run_id": "run_1", "status": "success"}]


class _FakeClient:
    def __init__(self) -> None:
        self.threads = _FakeThreads()
        self.runs = _FakeRuns()


@pytest.fixture
async def dispatched(
    monkeypatch: pytest.MonkeyPatch, registry_db: None
) -> AsyncIterator[list[dict[str, Any]]]:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://dash.test")
    client = _FakeClient()
    runs: list[dict[str, Any]] = []

    async def fake_create_durable_run(
        thread_id: str, assistant_id: str, **kwargs: Any
    ) -> dict[str, str]:
        runs.append({"thread_id": thread_id, "assistant_id": assistant_id, **kwargs})
        return {"run_id": "run_1"}

    async def fake_repo_access(full_name: str) -> str:
        return "workspace-app-token"

    monkeypatch.setattr(public_routes, "langgraph_client", lambda: client)
    monkeypatch.setattr(public_routes, "create_durable_run", fake_create_durable_run)
    monkeypatch.setattr(public_routes, "require_repo_access_for_workspace", fake_repo_access)
    await WORKSPACES.create(WorkspaceCreate(name="Core", repos=["acme/api"]), "admin")
    await WORKSPACES.create(WorkspaceCreate(name="OSS", repos=["acme/oss"]), "admin")
    yield runs


@pytest.fixture
async def api_client() -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(public_routes.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def _mint(workspace: str = "core") -> str:
    workspace_id = await WORKSPACES.id_for_slug(workspace)
    assert workspace_id is not None
    _, secret = await ApiKey.create(
        workspace_id=workspace_id,
        workspace=workspace,
        name="CI",
        expires_at=datetime.now(UTC) + timedelta(days=30),
        created_by="admin",
    )
    return secret


def _auth(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


async def test_started_threads_are_system_owned_and_carry_no_user(
    api_client: httpx.AsyncClient, dispatched: list[dict[str, Any]]
) -> None:
    secret = await _mint()

    response = await api_client.post(
        "/api/v1/threads",
        json={"prompt": "Upgrade the linter", "repo": "acme/api"},
        headers=_auth(secret),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["run_id"] == "run_1"
    assert body["url"] == f"http://dash.test/agents/{body['thread_id']}"

    metadata = public_routes.langgraph_client().threads.created[0]["metadata"]
    assert metadata["source"] == "api"
    assert metadata["owner_type"] == "system"
    assert metadata["visibility"] == "public"
    assert metadata["workspace"] == "core"
    assert metadata["title"] == "Upgrade the linter"
    assert metadata["created_by"] == "admin"
    assert (metadata["repo_owner"], metadata["repo_name"]) == ("acme", "api")
    assert "github_login" not in metadata
    assert "user_email" not in metadata

    configurable = dispatched[0]["config"]["configurable"]
    assert configurable["source"] == "api"
    assert configurable["workspace"] == "core"
    assert configurable["environment"] == "core"
    assert configurable["repo"] == {"owner": "acme", "name": "api"}
    assert configurable["invocation_id"]
    assert "github_login" not in configurable
    assert "user_email" not in configurable
    assert "admin_thread" not in configurable


async def test_a_repo_in_another_workspace_is_forbidden(
    api_client: httpx.AsyncClient, dispatched: list[dict[str, Any]]
) -> None:
    secret = await _mint()

    response = await api_client.post(
        "/api/v1/threads",
        json={"prompt": "Upgrade the linter", "repo": "acme/oss"},
        headers=_auth(secret),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "repository is not in this workspace"
    assert dispatched == []


async def test_bad_credentials_and_empty_prompts_are_refused(
    api_client: httpx.AsyncClient, dispatched: list[dict[str, Any]]
) -> None:
    secret = await _mint()

    missing = await api_client.post("/api/v1/threads", json={"prompt": "hi"})
    unknown = await api_client.post(
        "/api/v1/threads", json={"prompt": "hi"}, headers=_auth("osk_nope")
    )
    assert missing.status_code == 401
    assert unknown.status_code == 401
    assert missing.json()["detail"] == unknown.json()["detail"] == "invalid API key"

    blank = await api_client.post("/api/v1/threads", json={"prompt": "   "}, headers=_auth(secret))
    assert blank.status_code == 422
    assert dispatched == []


async def test_threads_are_only_readable_by_the_key_that_started_them(
    api_client: httpx.AsyncClient, dispatched: list[dict[str, Any]]
) -> None:
    mine = await _mint()
    theirs = await _mint()
    thread_id = (
        await api_client.post(
            "/api/v1/threads", json={"prompt": "Upgrade the linter"}, headers=_auth(mine)
        )
    ).json()["thread_id"]

    readable = await api_client.get(f"/api/v1/threads/{thread_id}", headers=_auth(mine))
    assert readable.status_code == 200
    assert readable.json() == {
        "thread_id": thread_id,
        "status": "finished",
        "title": "Upgrade the linter",
        "url": f"http://dash.test/agents/{thread_id}",
    }

    hidden = await api_client.get(f"/api/v1/threads/{thread_id}", headers=_auth(theirs))
    assert hidden.status_code == 404
