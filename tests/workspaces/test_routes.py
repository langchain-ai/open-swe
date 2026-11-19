from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from openswe.dashboard import deps, oauth, routes
from openswe.dashboard.execution_defaults import EXECUTION_DEFAULTS
from openswe.slack.channels import SlackChannel
from openswe.workspaces import refresh
from openswe.workspaces.store import WORKSPACES, WorkspaceCreate

_ADMIN_SESSION = {"sub": "admin", "email": "admin@example.com"}


@pytest.fixture
async def admin_client(
    monkeypatch: pytest.MonkeyPatch, registry_db: None
) -> AsyncIterator[httpx.AsyncClient]:
    """A client hitting the real dashboard app, signed in as an admin.

    Uses the aggregate dashboard router (mounted at ``/dashboard/api``) rather
    than the workspaces router alone, so origin-checked mutations behave as they
    do in production.
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


@pytest.mark.parametrize("enabled", [False, True])
async def test_repo_update_preserves_legacy_definition_and_refresh_mode(
    admin_client: httpx.AsyncClient, enabled: bool
) -> None:
    await EXECUTION_DEFAULTS.records.put("", {"enabled": enabled})
    await WORKSPACES.create(
        WorkspaceCreate(
            name="OSS",
            prompt="saved instructions",
            repos=["acme/oss"],
            setup_script="echo setup",
            inherit_default_sandbox=False,
        ),
        "admin",
    )
    refused = await admin_client.put(
        "/dashboard/api/workspaces/oss",
        json={"repos": ["acme/changed"], "prompt": "replace", "create_params": {}},
    )
    assert refused.status_code == 422
    with (
        patch.object(refresh, "ensure_refresh_cron", AsyncMock(return_value="cron-1")),
        patch.object(refresh, "start_refresh_run", AsyncMock(return_value="run-1")) as start,
    ):
        response = await admin_client.put(
            "/dashboard/api/workspaces/oss", json={"repos": ["acme/oss", "acme/api"]}
        )

    assert response.status_code == 200
    assert response.json()["prompt"] == "saved instructions"
    assert response.json()["setup_script"] == "echo setup"
    if enabled:
        assert response.json()["refresh_status"] == "never"
        start.assert_not_awaited()
    else:
        assert response.json()["refresh_status"] == "refreshing"
        assert response.json()["refresh_run_id"] == "run-1"
        start.assert_awaited_once_with("oss")


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
@pytest.mark.parametrize("mode", ["kitchen", "ask"])
async def test_newly_enabled_kitchen_channels_check_fresh_slack_eligibility(
    mode: str, admin_client: httpx.AsyncClient, payload: dict[str, bool], allowed: bool
) -> None:
    await WORKSPACES.create(
        WorkspaceCreate.model_validate(
            {
                "name": "OSS",
                "repos": ["acme/oss"],
                "slack_channel_ids": ["C0API", "C0NEW"],
                f"{mode}_channel_ids": ["C0API"],
            }
        ),
        "admin",
    )

    load = AsyncMock(return_value=SlackChannel(id="C0NEW", payload=payload))
    with patch.object(SlackChannel, "load", load):
        response = await admin_client.put(
            "/dashboard/api/workspaces/oss", json={f"{mode}_channel_ids": ["C0API", "C0NEW"]}
        )

    load.assert_awaited_once_with("C0NEW", use_cache=False)
    stored = (await admin_client.get("/dashboard/api/workspaces/oss")).json()
    if allowed:
        assert response.status_code == 200
        assert stored[f"{mode}_channel_ids"] == ["C0API", "C0NEW"]
    else:
        assert response.status_code == 400
        assert "C0NEW" in response.json()["detail"]
        assert stored[f"{mode}_channel_ids"] == ["C0API"]
