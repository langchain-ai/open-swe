from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from agent.analytics import queries
from agent.dashboard import routes
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

        app.dependency_overrides[routes.require_session] = lambda: {"sub": "user"}
        non_admin = await client.get("/dashboard/api/analytics/readiness")
        assert non_admin.status_code == 403

        app.dependency_overrides[routes.require_session] = lambda: {"sub": "admin"}
        admin = await client.get("/dashboard/api/analytics/readiness")
        assert admin.status_code == 200
        assert admin.json() == {"configured": True, "ready": True}

    readiness.assert_awaited_once()


@pytest.mark.parametrize("report", ["pr", "usage"])
@pytest.mark.parametrize("failure", ["disabled", "invalid_uri", "uninitialized", "database_down"])
async def test_pr_report_unavailability_is_distinct_from_empty_data(monkeypatch, failure, report):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.require_session] = lambda: {"sub": "user"}
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
        queries if report == "pr" else routes,
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
