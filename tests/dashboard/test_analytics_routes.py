from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from agent.analytics import queries
from agent.analytics import routes as analytics_routes
from agent.dashboard import oauth, routes
from agent.dashboard.workspace_settings import (
    WorkspaceSettingsUpdate,
    upsert_instance_settings,
)
from agent.database import analytics as database


@pytest.mark.asyncio
async def test_analytics_readiness_requires_admin(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(routes.router)
    readiness = AsyncMock(return_value={"configured": True, "ready": True})
    monkeypatch.setattr(database, "readiness", readiness)
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        anonymous = await client.get("/dashboard/api/analytics/readiness")
        assert anonymous.status_code == 401

        app.dependency_overrides[oauth.require_session] = lambda: {"sub": "user"}
        non_admin = await client.get("/dashboard/api/analytics/readiness")
        assert non_admin.status_code == 403

        app.dependency_overrides[oauth.require_session] = lambda: {"sub": "admin"}
        admin = await client.get("/dashboard/api/analytics/readiness")
        assert admin.status_code == 200
        assert admin.json() == {"configured": True, "ready": True}

    readiness.assert_awaited_once()


@pytest.mark.parametrize("report", ["pr", "usage"])
@pytest.mark.parametrize("failure", ["disabled", "invalid_uri", "uninitialized", "database_down"])
async def test_pr_report_unavailability_is_distinct_from_empty_data(monkeypatch, failure, report):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[oauth.require_session] = lambda: {"sub": "user"}
    if failure == "disabled":
        monkeypatch.delenv("POSTGRES_URI", raising=False)
    else:
        monkeypatch.setenv(
            "POSTGRES_URI", "invalid" if failure == "invalid_uri" else "postgresql://localhost/test"
        )
    error = (
        ConnectionError("database down")
        if failure == "database_down"
        else RuntimeError("not migrated")
    )
    monkeypatch.setattr(
        queries if report == "pr" else analytics_routes,
        "pr_merge_rate_by_model" if report == "pr" else "usage_leaderboard",
        AsyncMock(side_effect=error),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/dashboard/api/analytics/pr-merge-rate-by-model"
            if report == "pr"
            else "/dashboard/api/agent-usage-leaderboard"
        )
    assert response.status_code == 503
    assert response.json() == {
        "detail": f"{'PR' if report == 'pr' else 'Usage'} analytics is unavailable on this deployment."
    }


@pytest.mark.parametrize("viewer", ["admin", "member"])
@pytest.mark.parametrize("privacy", [True, False])
async def test_usage_leaderboard_applies_the_instance_policy_fresh_per_request(
    monkeypatch, fake_store, viewer, privacy
) -> None:
    """The route re-reads the instance record on every request (no settings cache),
    and only the policy-gated non-admin anonymizes other rows."""
    app = FastAPI()
    app.include_router(routes.router)
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    monkeypatch.setenv("POSTGRES_URI", "postgresql://localhost/test")
    leaderboard = AsyncMock(return_value={"rows": [], "total_members": 0})
    monkeypatch.setattr(analytics_routes, "usage_leaderboard", leaderboard)
    app.dependency_overrides[oauth.require_session] = lambda: {
        "sub": viewer,
        "email": f"{viewer}@example.com",
    }
    await upsert_instance_settings(
        WorkspaceSettingsUpdate(usage_leaderboard_privacy_enabled=privacy)
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/dashboard/api/agent-usage-leaderboard")

    assert response.status_code == 200
    assert response.json()["usage_leaderboard_privacy_enabled"] is privacy
    assert leaderboard.await_args is not None
    assert leaderboard.await_args.kwargs["admin"] is (viewer == "admin")
    assert leaderboard.await_args.kwargs["anonymize_others"] is (privacy and viewer != "admin")


@pytest.mark.parametrize("viewer", ["admin", "member"])
async def test_pr_report_exposes_attribution_diagnostics_to_admins_only(
    monkeypatch, viewer
) -> None:
    app = FastAPI()
    app.include_router(routes.router)
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    monkeypatch.setenv("POSTGRES_URI", "postgresql://localhost/test")
    report = AsyncMock(
        return_value={
            "status": "ready",
            "cohorts": [],
            "unavailable_thread_ids": ["0190a2f0-0000-7000-8000-000000000001"],
        }
    )
    monkeypatch.setattr(queries, "pr_merge_rate_by_model", report)
    app.dependency_overrides[oauth.require_session] = lambda: {"sub": viewer}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/dashboard/api/analytics/pr-merge-rate-by-model")

    assert response.status_code == 200
    assert report.await_args is not None
    assert report.await_args.kwargs["admin"] is (viewer == "admin")


@pytest.mark.parametrize("report", ["pr", "usage"])
async def test_pr_report_requires_session_before_checking_availability(monkeypatch, report):
    app = FastAPI()
    app.include_router(routes.router)
    monkeypatch.delenv("POSTGRES_URI", raising=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/dashboard/api/analytics/pr-merge-rate-by-model"
            if report == "pr"
            else "/dashboard/api/agent-usage-leaderboard"
        )
    assert response.status_code == 401
