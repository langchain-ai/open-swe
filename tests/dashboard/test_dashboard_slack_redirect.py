from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.dashboard import routes
from agent.dashboard.oauth import COOKIE_NAME, issue_session
from agent.slack.oauth import SLACK_STATE_COOKIE_NAME, SlackIdentity


@pytest.mark.parametrize("use_tunnel", [False, True])
def test_slack_callback_uses_registered_url_and_local_session(monkeypatch, use_tunnel) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://localhost:2024")
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "http://localhost:2024")
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret")
    callback_url = "http://localhost:2024/dashboard/api/slack/callback"
    if use_tunnel:
        callback_url = "https://test.ngrok-free.dev/dashboard/api/slack/callback"
        monkeypatch.setenv("SLACK_OAUTH_REDIRECT_URI", callback_url)
    else:
        monkeypatch.delenv("SLACK_OAUTH_REDIRECT_URI", raising=False)
    monkeypatch.setattr(routes, "slack_oauth_configured", lambda: True)

    exchanges: list[tuple[str, str]] = []
    links: list[dict[str, Any]] = []

    async def exchange(code: str, redirect_uri: str) -> str:
        exchanges.append((code, redirect_uri))
        return "test-access-token"

    async def identity(access_token: str) -> SlackIdentity:
        return SlackIdentity("U123", "T1", "alice@example.com", True, "Alice")

    async def link(**kwargs: Any) -> None:
        links.append(kwargs)

    monkeypatch.setattr(routes, "exchange_slack_code", exchange)
    monkeypatch.setattr(routes, "fetch_slack_identity", identity)
    monkeypatch.setattr(routes, "verify_team", lambda identity: None)
    monkeypatch.setattr(routes, "upsert_mapping", link)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app, base_url="http://localhost:2024") as client:
        client.cookies.set(COOKIE_NAME, issue_session(login="alice", email=None, avatar_url=None))
        login = client.get("/dashboard/api/slack/login", follow_redirects=False)
        assert login.status_code == 302
        authorize = parse_qs(urlparse(login.headers["location"]).query)
        assert authorize["redirect_uri"] == [callback_url]
        assert "Secure" not in login.headers["set-cookie"]
        params = {"code": "slack-code", "state": authorize["state"][0]}

        # The public relay returns the query to localhost; it must not bypass
        # either the local session or the browser-bound OAuth state check.
        with TestClient(app, base_url="http://localhost:2024") as stranger:
            missing_session = stranger.get("/dashboard/api/slack/callback", params=params)
            assert missing_session.status_code == 401
            stranger.cookies.set(COOKIE_NAME, client.cookies[COOKIE_NAME])
            missing_nonce = stranger.get("/dashboard/api/slack/callback", params=params)
            assert missing_nonce.status_code == 400
            stranger.cookies.set(SLACK_STATE_COOKIE_NAME, "wrong-nonce")
            wrong_nonce = stranger.get("/dashboard/api/slack/callback", params=params)
            assert wrong_nonce.status_code == 400
        assert exchanges == []
        assert links == []

        callback = client.get(
            "/dashboard/api/slack/callback", params=params, follow_redirects=False
        )
        assert callback.status_code == 302
        assert callback.headers["location"] == "http://localhost:2024/my-settings"
        assert exchanges == [("slack-code", callback_url)]
        assert links == [
            {
                "github_login": "alice",
                "work_email": "alice@example.com",
                "slack_user_id": "U123",
                "source": "slack_oauth",
                "status": "active",
            }
        ]
        assert SLACK_STATE_COOKIE_NAME not in client.cookies
