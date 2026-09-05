"""The desktop Slack connect flow hands off through the loopback port.

Consent runs in the user's own browser, which holds neither the desktop app's
session cookie nor the flow's state cookie — the mismatch that made connecting
fail with "oauth state mismatch". What replaces the cookie check is a PKCE
handoff whose code says what was connected but never whose account to connect
it to, so the link can only ever land on the session the app itself holds.
"""

import base64
import hashlib
from typing import Any
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.dashboard import routes
from agent.dashboard.oauth import COOKIE_NAME, issue_session
from agent.slack.oauth import SlackIdentity

_VERIFIER = "desktop-connect-verifier"
_CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(_VERIFIER.encode()).digest()).decode().rstrip("=")
)
_APP_ORIGIN = {"origin": "open-swe://app"}


@pytest.fixture
def links(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Stub the Slack legs and collect the account links they produce."""
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret")

    async def fake_exchange(code: str, redirect_uri: str) -> str:
        return "xoxp-test"

    async def fake_identity(access_token: str) -> SlackIdentity:
        return SlackIdentity(
            user_id="U123",
            team_id="T1",
            email="alice@slack.example",
            email_verified=True,
            name="alice",
        )

    collected: list[dict[str, Any]] = []

    async def fake_upsert_mapping(**kwargs: Any) -> None:
        collected.append(kwargs)

    monkeypatch.setattr(routes, "slack_oauth_configured", lambda: True)
    monkeypatch.setattr(
        routes,
        "build_authorize_url",
        lambda *, redirect_uri, state: f"https://slack.example/authorize?state={state}",
    )
    monkeypatch.setattr(routes, "exchange_slack_code", fake_exchange)
    monkeypatch.setattr(routes, "fetch_slack_identity", fake_identity)
    monkeypatch.setattr(routes, "verify_team", lambda identity: None)
    monkeypatch.setattr(routes, "upsert_mapping", fake_upsert_mapping)
    return collected


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app, base_url="https://dashboard.example")


def _start_desktop_slack_flow(client: TestClient) -> str:
    """Run the login and callback legs; return the loopback handoff code."""
    login = client.get(
        "/dashboard/api/slack/login",
        params={"desktop_handoff": _CHALLENGE, "desktop_port": 51234},
        follow_redirects=False,
    )
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]

    # The browser that follows this carries no state cookie, as a real one won't.
    callback = _client().get(
        "/dashboard/api/slack/callback",
        params={"code": "slack-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    location = urlparse(callback.headers["location"])
    assert (location.scheme, location.netloc, location.path) == (
        "http",
        "127.0.0.1:51234",
        "/callback",
    )
    return parse_qs(location.query)["code"][0]


def test_desktop_slack_connect_links_under_the_session_the_app_holds(
    links: list[dict[str, Any]],
) -> None:
    with _client() as client:
        client.cookies.set(COOKIE_NAME, issue_session(login="alice", email=None, avatar_url=None))
        handoff = _start_desktop_slack_flow(client)
        assert links == [], "the callback alone must not link an account"

        wrong_verifier = client.post(
            "/dashboard/api/slack/desktop/exchange",
            json={"code": handoff, "verifier": "not-the-verifier"},
            headers=_APP_ORIGIN,
        )
        assert wrong_verifier.status_code == 400

        exchange = client.post(
            "/dashboard/api/slack/desktop/exchange",
            json={"code": handoff, "verifier": _VERIFIER},
            headers=_APP_ORIGIN,
        )
        assert exchange.status_code == 200
        assert links == [
            {
                "github_login": "alice",
                "work_email": "alice@slack.example",
                "slack_user_id": "U123",
                "source": "slack_oauth",
                "status": "active",
            }
        ]

    # A JWT is signed but not encrypted, and this one rides in a URL the browser
    # records — so a login in there would let anyone who saw it attach their own
    # Slack account to that login instead.
    payload = jwt.decode(handoff, "test-secret", algorithms=["HS256"])
    assert "alice" not in payload.values()
    assert set(payload) == {"slack_user_id", "email", "provider", "challenge", "iat", "exp"}
