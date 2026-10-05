import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from agent.dashboard import deps, oauth, routes
from agent.slack.channels import SlackChannel
from agent.workspaces import routes as workspace_routes
from agent.workspaces.store import WORKSPACES, WorkspaceCreate

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


@pytest.mark.parametrize(
    ("setup_script", "run_id"), [("", None), ("echo setup", "run-1"), ("echo setup", None)]
)
async def test_create_starts_initial_build_when_setup_is_provided(
    admin_client: httpx.AsyncClient, setup_script: str, run_id: str | None
) -> None:
    with (
        patch.object(
            workspace_routes, "ensure_refresh_cron", AsyncMock(return_value="cron-1")
        ) as cron,
        patch.object(
            workspace_routes, "start_refresh_run", AsyncMock(return_value=run_id)
        ) as start,
    ):
        response = await admin_client.post(
            "/dashboard/api/workspaces",
            json={
                "name": "OSS",
                "repos": ["acme/oss"],
                "inherit_default_sandbox": False,
                "setup_script": setup_script,
                "update_script": "echo update",
            },
        )

    stored = await WORKSPACES.get("oss")
    assert stored is not None
    assert stored.setup_script == setup_script
    assert stored.update_script == "echo update"
    if not setup_script:
        assert response.status_code == 200
        assert response.json()["refresh_status"] == "never"
        cron.assert_not_awaited()
        start.assert_not_awaited()
    else:
        cron.assert_awaited_once_with("oss")
        start.assert_awaited_once_with("oss")
        if run_id is None:
            assert response.status_code == 502
            assert response.json()["detail"] == (
                "workspace was created but its initial image build could not start; "
                "retry the build from workspace settings"
            )
        else:
            assert response.status_code == 200
            assert response.json()["refresh_status"] == "refreshing"
            assert response.json()["refresh_run_id"] == run_id


async def test_repo_update_starts_snapshot_rebuild(admin_client: httpx.AsyncClient) -> None:
    await WORKSPACES.create(
        WorkspaceCreate(
            name="OSS", repos=["acme/oss"], setup_script="echo setup", inherit_default_sandbox=False
        ),
        "admin",
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


_ELIGIBLE = {"is_member": True, "is_ext_shared": False, "is_pending_ext_shared": False}


@pytest.mark.parametrize(
    ("payload", "allowed"),
    [
        (_ELIGIBLE, True),
        ({**_ELIGIBLE, "is_pending_ext_shared": True}, False),
        ({**_ELIGIBLE, "is_ext_shared": True}, False),
        ({**_ELIGIBLE, "is_member": False}, False),
    ],
)
async def test_newly_enabled_kitchen_channels_check_fresh_slack_eligibility(
    admin_client: httpx.AsyncClient, payload: dict[str, bool], allowed: bool
) -> None:
    with patch.object(
        workspace_routes.SlackChannel,
        "load",
        AsyncMock(return_value=SlackChannel(id="C0API", payload=_ELIGIBLE)),
    ):
        created = await admin_client.post(
            "/dashboard/api/workspaces",
            json={
                "name": "OSS",
                "repos": ["acme/oss"],
                "slack_channel_ids": ["C0API", "C0NEW"],
                "kitchen_channel_ids": ["C0API"],
            },
        )
    assert created.status_code == 200

    load = AsyncMock(return_value=SlackChannel(id="C0NEW", payload=payload))
    with patch.object(workspace_routes.SlackChannel, "load", load):
        response = await admin_client.put(
            "/dashboard/api/workspaces/oss", json={"kitchen_channel_ids": ["C0API", "C0NEW"]}
        )

    load.assert_awaited_once_with("C0NEW", use_cache=False)
    stored = (await admin_client.get("/dashboard/api/workspaces/oss")).json()
    if allowed:
        assert response.status_code == 200
        assert stored["kitchen_channel_ids"] == ["C0API", "C0NEW"]
    else:
        assert response.status_code == 400
        assert "C0NEW" in response.json()["detail"]
        assert stored["kitchen_channel_ids"] == ["C0API"]


async def test_a_prompt_edit_during_a_refresh_outlives_it(
    admin_client: httpx.AsyncClient,
) -> None:
    await WORKSPACES.create(
        WorkspaceCreate(name="Core", repos=["acme/api"], setup_script="echo tools"), "admin"
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


async def test_deleting_the_default_workspace_is_a_409(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.delete("/dashboard/api/workspaces/default")

    assert response.status_code == 409
    assert (await admin_client.get("/dashboard/api/workspaces/default")).status_code == 200
