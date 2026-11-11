"""Workspace-bound navigation, without looking up private conversations."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from openswe.dashboard.oauth import require_session
from openswe.slack import dashboard_routes


@pytest.fixture
async def navigation(monkeypatch: pytest.MonkeyPatch):
    @asynccontextmanager
    async def bot():
        yield object()

    monkeypatch.setattr(dashboard_routes.SlackClient, "bot", bot)
    identity = AsyncMock(return_value={"url": "https://example.slack.com/", "team_id": "T123"})
    monkeypatch.setattr(dashboard_routes, "slack_identity", identity)
    app = FastAPI()
    app.include_router(dashboard_routes.router, prefix="/dashboard/api")
    app.dependency_overrides[require_session] = lambda: {"sub": "alice"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://robin.example"
    ) as client:
        yield app, client, identity


@pytest.mark.parametrize(
    ("params", "target"),
    [
        ({"channel": "C123"}, "https://example.slack.com/archives/C123"),
        (
            {"channel": "C123", "message_ts": "1700000000.1"},
            "https://example.slack.com/archives/C123/p1700000000100000",
        ),
        (
            {
                "channel": "D123",
                "message_ts": "1700000000.123456",
                "thread_ts": "1699999999.000001",
            },
            "https://example.slack.com/archives/D123/p1700000000123456?thread_ts=1699999999.000001&cid=D123",
        ),
    ],
)
async def test_navigation_targets_the_bot_workspace_and_exact_message(navigation, params, target):
    _, client, _ = navigation
    response = await client.get("/dashboard/api/slack/open", params=params)
    assert response.status_code == 302
    assert response.headers["location"] == target
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize(
    "workspace_url",
    [
        None,
        "https://slack.com/",
        "https://app.slack.com/",
        "http://example.slack.com/",
        "https://example.slack.com.evil.test/",
        "https://user@example.slack.com/",
        "https://example.slack.com:8443/",
        "https://example.slack.com/redirect?url=https://evil.test",
    ],
)
async def test_navigation_has_no_ambiguous_or_untrusted_origin_fallback(navigation, workspace_url):
    _, client, identity = navigation
    identity.return_value = {"url": workspace_url, "team_id": "T123"}
    response = await client.get("/dashboard/api/slack/open", params={"channel": "C123"})
    assert response.status_code == 503
    assert "location" not in response.headers


@pytest.mark.parametrize(
    "params",
    [
        {"channel": "//evil.test"},
        {"channel": "C123", "message_ts": "1/../../"},
        {"channel": "C123", "thread_ts": "1&url=https://evil.test"},
    ],
)
async def test_navigation_rejects_invalid_locations_before_calling_slack(navigation, params):
    _, client, identity = navigation
    response = await client.get("/dashboard/api/slack/open", params=params)
    assert response.status_code == 422
    identity.assert_not_awaited()


async def test_navigation_requires_dashboard_authentication(navigation):
    app, client, identity = navigation
    app.dependency_overrides.clear()
    response = await client.get("/dashboard/api/slack/open", params={"channel": "C123"})
    assert response.status_code == 401
    identity.assert_not_awaited()
