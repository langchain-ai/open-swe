from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from agent.dashboard import deps, oauth, routes

# Workspaces created through the HTTP API now live in PostgreSQL.
pytestmark = pytest.mark.usefixtures("registry_db")

_ADMIN_SESSION = {"sub": "admin", "email": "admin@example.com"}


@pytest.fixture
async def admin_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    """A client hitting the real dashboard app, signed in as an admin.

    Uses the aggregate dashboard router (mounted at ``/dashboard/api``, same as
    ``tests/dashboard/test_workspace_mcps.py``) rather than the workspaces
    router alone, so origin-checked mutations behave as they do in production.
    """
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://test")
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[deps.admin_session] = lambda: _ADMIN_SESSION
    app.dependency_overrides[oauth.require_session] = lambda: _ADMIN_SESSION
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://test"},
    ) as client:
        yield client


async def test_duplicate_repo_is_a_409(admin_client: httpx.AsyncClient) -> None:
    first = await admin_client.post(
        "/dashboard/api/workspaces", json={"name": "Core", "repos": ["acme/api"]}
    )
    assert first.status_code == 200
    second = await admin_client.post(
        "/dashboard/api/workspaces", json={"name": "OSS", "repos": ["acme/api"]}
    )
    assert second.status_code == 409
    assert "already belongs to workspace core" in second.json()["detail"]


async def test_duplicate_repo_on_update_is_a_409(admin_client: httpx.AsyncClient) -> None:
    await admin_client.post(
        "/dashboard/api/workspaces", json={"name": "Core", "repos": ["acme/api"]}
    )
    await admin_client.post(
        "/dashboard/api/workspaces", json={"name": "OSS", "repos": ["acme/oss"]}
    )
    response = await admin_client.put("/dashboard/api/workspaces/oss", json={"repos": ["acme/api"]})
    assert response.status_code == 409
    assert "already belongs to workspace core" in response.json()["detail"]


async def test_options_carry_repos_channels_and_default_flag(
    admin_client: httpx.AsyncClient,
) -> None:
    await admin_client.post("/dashboard/api/workspaces", json={"name": "Default"})
    await admin_client.post(
        "/dashboard/api/workspaces",
        json={"name": "OSS", "repos": ["acme/oss"], "slack_channel_ids": ["C0SS"]},
    )
    body = (await admin_client.get("/dashboard/api/workspaces/options")).json()
    by_slug = {item["slug"]: item for item in body["workspaces"]}
    assert by_slug["default"]["is_default"] is True and by_slug["default"]["repos"] == []
    assert by_slug["oss"]["repos"] == ["acme/oss"] and by_slug["oss"]["slack_channel_ids"] == [
        "C0SS"
    ]


async def test_a_workspace_with_no_repository_is_a_400(admin_client: httpx.AsyncClient) -> None:
    """Only `default` may claim nothing; a malformed definition is not a conflict."""
    response = await admin_client.post("/dashboard/api/workspaces", json={"name": "OSS"})
    assert response.status_code == 400
    assert "at least one repository" in response.json()["detail"]


async def test_a_repeated_workspace_name_is_a_409(admin_client: httpx.AsyncClient) -> None:
    payload = {"name": "Core", "repos": ["acme/api"]}
    assert (await admin_client.post("/dashboard/api/workspaces", json=payload)).status_code == 200
    second = await admin_client.post(
        "/dashboard/api/workspaces", json={"name": "Core", "repos": ["acme/other"]}
    )
    assert second.status_code == 409
    assert "already exists" in second.json()["detail"]
