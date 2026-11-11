from contextlib import nullcontext
from typing import Literal
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse
from uuid import uuid7

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openswe.dashboard import auth_routes, routes
from openswe.dashboard.langsmith_login import LangSmithAccount, LangSmithLogin
from openswe.dashboard.oauth import COOKIE_NAME, decode_state, issue_session
from openswe.slack import connect, oauth
from openswe.users import User
from openswe.users.models import UserIdentity


@pytest.fixture(autouse=True)
def _valid_langsmith_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LangSmithLogin, "authorize_user", AsyncMock(return_value="ls_token"))


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
    user = User()
    link = AsyncMock(return_value=user)
    monkeypatch.setattr(user, "link", link)
    monkeypatch.setattr(User, "for_session", AsyncMock(return_value=user))
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
        assert callback.headers["location"] == f"{local_url}/my-settings/connections"
    exchange.assert_awaited_once_with("code", expected_callback)
    link.assert_awaited_once_with("slack", "U123", email="alice@example.com", team_id="T123")


@pytest.mark.parametrize(
    ("redirect_to", "expected_path", "provider", "onboarding", "swap_session"),
    [
        (None, "/my-settings/connections", "github", False, False),
        (None, "/my-settings/connections", "langsmith", False, False),
        (None, "/my-settings/connections", "langsmith", True, False),
        (None, "", "langsmith", True, True),
        ("/agents?view=mine#recent", "/agents?view=mine#recent", "github", False, False),
        ("/my-settings/connections", "/my-settings/connections", "github", False, False),
        ("https://evil.example/steal", "", "github", False, False),
        ("/dashboard/api/slack/login", "", "github", False, False),
    ],
)
def test_slack_callback_links_the_slack_identity_to_the_session_user(
    monkeypatch: pytest.MonkeyPatch,
    redirect_to: str | None,
    expected_path: str,
    provider: Literal["github", "langsmith"],
    onboarding: bool,
    swap_session: bool,
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

    user = User(identities=[UserIdentity(provider="langsmith", external_id="ls-person")])
    link = AsyncMock(return_value=user)
    monkeypatch.setattr(user, "link", link)
    for_session = AsyncMock(return_value=user)
    monkeypatch.setattr(User, "for_session", for_session)
    monkeypatch.setattr(User, "get", AsyncMock(return_value=user))

    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "N9uTl4bo9zqYxvGhI6GV2Rb7YmHJdfdHIIO_6Zpf_4I=")
    monkeypatch.setattr(
        LangSmithLogin,
        "metadata",
        AsyncMock(return_value={"authorization_endpoint": "https://smith.test/authorize"}),
    )
    monkeypatch.setattr(
        LangSmithLogin,
        "complete",
        AsyncMock(return_value=(LangSmithAccount(sub="ls-person", ls_org_id="org"), {}, "token")),
    )
    monkeypatch.setattr(LangSmithLogin, "save", AsyncMock())
    monkeypatch.setattr(auth_routes, "refresh_guard", lambda *_args: nullcontext())
    monkeypatch.setattr(User, "sign_in", AsyncMock(return_value=user))
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app, base_url=local_url) as client:
        client.cookies.set(
            COOKIE_NAME,
            issue_session(
                login=f"user:{user.id}" if provider == "langsmith" else "alice",
                email=None,
                avatar_url=None,
                user_id=str(user.id),
                provider=provider,
                langsmith_subject="ls-person" if provider == "langsmith" else None,
            ),
        )
        if onboarding:
            client.cookies.clear()
            desktop = client.get(
                "/dashboard/api/auth/langsmith/login",
                params={"connect_slack": "true", "desktop": "true"},
            )
            assert desktop.status_code == 400
            start = client.get(LangSmithLogin.slack_onboarding_url(), follow_redirects=False)
            assert start.status_code == 302
            langsmith_state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
            assert decode_state(langsmith_state)["connect_slack"] is True
            signed_in = client.get(
                "/dashboard/api/langsmith/callback",
                params={"state": langsmith_state, "code": "langsmith-code"},
                follow_redirects=False,
            )
            assert signed_in.status_code == 302, signed_in.text
            assert signed_in.headers["location"] == f"{local_url}/dashboard/api/slack/login"
            assert COOKIE_NAME in client.cookies
        login = client.get(
            "/dashboard/api/slack/login",
            params={"redirect_to": redirect_to} if redirect_to is not None else {},
            follow_redirects=False,
        )
        assert login.status_code == 302, login.text
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        if swap_session:
            monkeypatch.setattr(
                User,
                "get",
                AsyncMock(
                    return_value=User(
                        identities=[UserIdentity(provider="langsmith", external_id="ls-person")]
                    )
                ),
            )
        callback = client.get(
            "/dashboard/api/slack/callback",
            params={"code": "code", "state": state},
            follow_redirects=False,
        )
        if swap_session:
            assert callback.status_code == 400
            link.assert_not_awaited()
            return
        assert callback.status_code == 302, callback.text
        assert callback.headers["location"] == f"{local_url}{expected_path}"

    if provider == "github":
        for_session.assert_awaited_with(user.id, "alice")
    else:
        for_session.assert_not_awaited()
    link.assert_awaited_once_with("slack", "U123", email="alice@example.com", team_id="T123")
