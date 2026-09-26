import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from agent.dashboard import deps, oauth, routes
from agent.dashboard.workspace_settings import (
    WorkspaceSettingsUpdate,
    upsert_instance_settings,
    upsert_workspace_overrides,
)
from agent.workspaces import routes as workspace_routes
from agent.workspaces.store import WORKSPACES
from tests.conftest import FakeStore

_ADMIN_SESSION = {"sub": "admin", "email": "admin@example.com"}


@pytest.fixture
async def admin_client(
    monkeypatch: pytest.MonkeyPatch, registry_db: None
) -> AsyncIterator[httpx.AsyncClient]:
    """A client hitting the real dashboard app, signed in as an admin.

    Uses the aggregate dashboard router (mounted at ``/dashboard/api``, same as
    ``tests/dashboard/test_workspace_mcps.py``) rather than the workspaces
    router alone, so origin-checked mutations behave as they do in production.
    Carries ``registry_db`` because every workspace this writes is a row.
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


async def test_repo_update_starts_snapshot_rebuild(admin_client: httpx.AsyncClient) -> None:
    await admin_client.post(
        "/dashboard/api/workspaces",
        json={"name": "OSS", "repos": ["acme/oss"], "setup_script": "echo setup"},
    )
    with (
        patch.object(workspace_routes, "ensure_refresh_cron", AsyncMock(return_value="cron-1")),
        patch.object(
            workspace_routes, "start_refresh_run", AsyncMock(return_value="run-1")
        ) as start,
    ):
        response = await admin_client.put(
            "/dashboard/api/workspaces/oss", json={"repos": ["acme/oss", "acme/api"]}
        )

    assert response.status_code == 200
    assert response.json()["refresh_status"] == "refreshing"
    assert response.json()["refresh_run_id"] == "run-1"
    start.assert_awaited_once_with("oss")


async def test_non_repo_update_does_not_rebuild_snapshot(admin_client: httpx.AsyncClient) -> None:
    await admin_client.post(
        "/dashboard/api/workspaces",
        json={"name": "OSS", "repos": ["acme/oss"], "setup_script": "echo setup"},
    )
    with (
        patch.object(workspace_routes, "ensure_refresh_cron", AsyncMock(return_value="cron-1")),
        patch.object(workspace_routes, "start_refresh_run", AsyncMock()) as start,
    ):
        response = await admin_client.put(
            "/dashboard/api/workspaces/oss", json={"prompt": "Be concise"}
        )

    assert response.status_code == 200
    start.assert_not_awaited()


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


async def test_options_carry_each_workspace_default_repository(
    admin_client: httpx.AsyncClient, fake_store: FakeStore
) -> None:
    """The composer preselects a workspace's default repository when the workspace is picked first.

    Every workspace can use every repository, so an inherited default applies
    even where another workspace prefers that repository.
    """
    await admin_client.post("/dashboard/api/workspaces", json={"name": "Default"})
    await admin_client.post(
        "/dashboard/api/workspaces", json={"name": "OSS", "repos": ["acme/oss"]}
    )
    await admin_client.post(
        "/dashboard/api/workspaces", json={"name": "Core", "repos": ["acme/api"]}
    )
    await upsert_instance_settings(WorkspaceSettingsUpdate(default_repo="acme/oss"))
    await upsert_workspace_overrides("core", WorkspaceSettingsUpdate(default_repo="acme/api"))

    body = (await admin_client.get("/dashboard/api/workspaces/options")).json()

    by_slug = {item["slug"]: item for item in body["workspaces"]}
    assert by_slug["oss"]["default_repo"] == "acme/oss"
    assert by_slug["core"]["default_repo"] == "acme/api"
    assert by_slug["default"]["default_repo"] == "acme/oss"


async def test_a_workspace_may_prefer_no_repository(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.post("/dashboard/api/workspaces", json={"name": "OSS"})
    assert response.status_code == 200
    assert response.json()["repos"] == []


@pytest.mark.parametrize("stale_precheck", [False, True])
async def test_a_repeated_workspace_name_is_a_409(
    admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, stale_precheck: bool
) -> None:
    payload = {"name": "Core", "repos": ["acme/api"]}
    first = await admin_client.post("/dashboard/api/workspaces", json=payload)
    assert first.status_code == 200
    if stale_precheck:

        async def slug_was_free(slug: str) -> bool:
            return False

        monkeypatch.setattr(WORKSPACES, "slug_exists", slug_was_free)
    second = await admin_client.post(
        "/dashboard/api/workspaces", json={"name": "Core", "repos": ["acme/other"]}
    )
    assert second.status_code == 409
    assert "already exists" in second.json()["detail"]
    stored = await admin_client.get("/dashboard/api/workspaces/core")
    assert stored.json() == first.json()


async def test_two_creates_of_one_name_at_once_are_a_200_and_a_409(
    admin_client: httpx.AsyncClient,
) -> None:
    """The loser of the race lands on the unique constraint, not on a 500."""
    payload = {"name": "Core", "repos": ["acme/api"]}
    first, second = await asyncio.gather(
        admin_client.post("/dashboard/api/workspaces", json=payload),
        admin_client.post("/dashboard/api/workspaces", json=payload),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 409]
    conflict = first if first.status_code == 409 else second
    assert "already" in conflict.json()["detail"]


async def test_a_prompt_edit_during_a_refresh_outlives_it(
    admin_client: httpx.AsyncClient,
) -> None:
    await admin_client.post(
        "/dashboard/api/workspaces",
        json={"name": "Core", "repos": ["acme/api"], "setup_script": "echo tools"},
    )
    await WORKSPACES.mark_refreshing("core")
    response = await admin_client.put("/dashboard/api/workspaces/core", json={"prompt": "new"})
    assert response.status_code == 200
    assert response.json()["refresh_status"] == "refreshing"

    await WORKSPACES.start_refresh_step("core", "capture")
    await WORKSPACES.mark_refresh_settled("core", "success")

    stored = (await admin_client.get("/dashboard/api/workspaces/core")).json()
    assert stored["prompt"] == "new"
    assert stored["refresh_status"] == "success"
