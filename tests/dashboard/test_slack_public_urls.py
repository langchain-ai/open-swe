from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse
from uuid import uuid7

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.dashboard import routes
from agent.dashboard.oauth import COOKIE_NAME, issue_session
from agent.slack import connect, oauth
from agent.users import User


@pytest.mark.parametrize("public_url", [None, "https://example.ngrok-free.dev/"])
def test_slack_public_url_applies_to_manifest_and_oauth_without_changing_local_cookies(
    monkeypatch: pytest.MonkeyPatch, public_url: str | None
) -> None:
    local_url = "http://localhost:2024"
    monkeypatch.setenv("DASHBOARD_BASE_URL", local_url)
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", local_url)
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-session-signing-key-at-least-32-bytes")
    if public_url is None:
        monkeypatch.delenv("SLACK_PUBLIC_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("SLACK_PUBLIC_BASE_URL", public_url)
    monkeypatch.setattr(oauth, "SLACK_CLIENT_ID", "test-client")
    monkeypatch.setattr(oauth, "SLACK_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(oauth, "SLACK_TEAM_ID", "")
    exchange = AsyncMock(return_value="slack-test-token")
    monkeypatch.setattr(connect, "exchange_slack_code", exchange)
    monkeypatch.setattr(
        connect,
        "fetch_slack_identity",
        AsyncMock(
            return_value=oauth.SlackIdentity(
                user_id="U123",
                team_id="T123",
                email="alice@example.com",
                email_verified=True,
                name="Alice",
            )
        ),
    )
    save = AsyncMock()
    monkeypatch.setattr(connect, "upsert_mapping", save)
    monkeypatch.setattr(connect.User, "get", AsyncMock(return_value=None))
    app = FastAPI()
    app.include_router(routes.router)
    expected_base = (public_url or local_url).rstrip("/")
    expected_callback = f"{expected_base}/dashboard/api/slack/callback"
    with TestClient(app, base_url=local_url) as client:
        client.cookies.set(
            COOKIE_NAME,
            issue_session(login="alice", email=None, avatar_url=None, user_id=str(uuid7())),
        )
        settings = client.get("/dashboard/api/me").json()
        assert settings["api_base_url"] == local_url
        assert settings.get("slack_base_url") == expected_base
        login = client.get("/dashboard/api/slack/login", follow_redirects=False)
        assert login.status_code == 302
        params = parse_qs(urlparse(login.headers["location"]).query)
        assert params["redirect_uri"] == [expected_callback]
        assert "Secure" not in login.headers["set-cookie"]
        # ngrok returns the browser to this local callback, preserving code and state.
        callback = client.get(
            "/dashboard/api/slack/callback",
            params={"code": "code", "state": params["state"][0]},
            follow_redirects=False,
        )
        assert callback.status_code == 302, callback.text
        assert callback.headers["location"] == f"{local_url}/my-settings"
    exchange.assert_awaited_once_with("code", expected_callback)
    assert save.await_args is not None
    assert save.await_args.kwargs["github_login"] == "alice"
    assert save.await_args.kwargs["slack_user_id"] == "U123"


def test_slack_callback_links_the_slack_identity_to_the_session_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_url = "http://localhost:2024"
    monkeypatch.setenv("DASHBOARD_BASE_URL", local_url)
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", local_url)
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-session-signing-key-at-least-32-bytes")
    monkeypatch.delenv("SLACK_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setattr(oauth, "SLACK_CLIENT_ID", "test-client")
    monkeypatch.setattr(oauth, "SLACK_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(oauth, "SLACK_TEAM_ID", "")
    monkeypatch.setattr(connect, "exchange_slack_code", AsyncMock(return_value="slack-test-token"))
    monkeypatch.setattr(
        connect,
        "fetch_slack_identity",
        AsyncMock(
            return_value=oauth.SlackIdentity(
                user_id="U123",
                team_id="T123",
                email="alice@example.com",
                email_verified=True,
                name="Alice",
            )
        ),
    )
    monkeypatch.setattr(connect, "upsert_mapping", AsyncMock())

    user = User()
    link = AsyncMock(return_value=user)
    monkeypatch.setattr(user, "link", link)
    get_user = AsyncMock(return_value=user)
    monkeypatch.setattr(connect.User, "get", get_user)

    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app, base_url=local_url) as client:
        client.cookies.set(
            COOKIE_NAME,
            issue_session(login="alice", email=None, avatar_url=None, user_id=str(user.id)),
        )
        login = client.get("/dashboard/api/slack/login", follow_redirects=False)
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        callback = client.get(
            "/dashboard/api/slack/callback",
            params={"code": "code", "state": state},
            follow_redirects=False,
        )
        assert callback.status_code == 302, callback.text

    get_user.assert_awaited_once_with(user.id)
    link.assert_awaited_once_with("slack", "U123", team_id="T123")
