from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from agent.analytics import database
from agent.dashboard import routes


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
