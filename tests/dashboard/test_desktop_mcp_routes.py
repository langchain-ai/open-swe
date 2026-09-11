from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.dashboard import desktop_mcps, routes
from agent.dashboard.oauth import COOKIE_NAME, issue_session


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret")
    monkeypatch.setattr(
        desktop_mcps,
        "desktop_mcp_catalog",
        AsyncMock(return_value={"login": "alice", "tools": []}),
    )
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_desktop_mcp_catalog_requires_desktop_session(client: TestClient) -> None:
    client.cookies.set(
        COOKIE_NAME,
        issue_session(login="alice", email=None, avatar_url=None),
    )
    assert client.get("/dashboard/api/desktop/mcps").status_code == 403

    client.cookies.set(
        COOKIE_NAME,
        issue_session(login="alice", email=None, avatar_url=None, desktop=True),
    )
    assert client.get("/dashboard/api/desktop/mcps").status_code == 403

    response = client.get(
        "/dashboard/api/desktop/mcps",
        headers={"origin": "open-swe://app"},
    )
    assert response.status_code == 200
    assert response.json() == {"login": "alice", "tools": []}
