import base64
import hashlib
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openswe.dashboard import auth_routes, github_link, routes
from openswe.dashboard.oauth import (
    COOKIE_NAME,
    GithubUser,
    decode_session,
    issue_session,
    sanitize_redirect_to,
)
from openswe.dashboard.principal import DashboardPrincipal
from openswe.users import User
from openswe.users.models import UserIdentity


@pytest.mark.parametrize(
    ("configured", "unavailable", "status"),
    [(True, False, 401), (True, True, 200), (False, False, 200)],
)
def test_me_reauthenticates_only_a_confirmed_missing_account(
    monkeypatch: pytest.MonkeyPatch, configured: bool, unavailable: bool, status: int
) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-session-signing-key-at-least-32-bytes")
    monkeypatch.setattr(auth_routes.postgres, "configured", lambda: configured)
    monkeypatch.setattr(
        auth_routes.User,
        "for_session",
        AsyncMock(
            return_value=None,
            side_effect=RuntimeError("database unavailable") if unavailable else None,
        ),
    )
    monkeypatch.setattr(
        auth_routes,
        "get_user_preferences",
        AsyncMock(return_value={"follow_up_behavior": "queue"}),
    )
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        client.cookies.set(
            COOKIE_NAME,
            issue_session(login="alice", email=None, avatar_url=None, user_id=str(User().id)),
        )
        response = client.get("/dashboard/api/me")

    assert response.status_code == status
    if status == 200:
        assert response.json()["login"] == "alice"


def test_sanitize_redirect_to_preserves_allowed_dashboard_target(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_ALLOWED_ORIGINS", "https://preview.example")

    target = "https://dashboard.example/agents/thread-1/plan?from=slack#review"

    assert sanitize_redirect_to(target) == target


def test_sanitize_redirect_to_preserves_allowed_preview_target(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_ALLOWED_ORIGINS", "https://preview.example")

    target = "https://preview.example/agents/thread-1/plan?from=slack#review"

    assert sanitize_redirect_to(target) == target


def test_sanitize_redirect_to_preserves_safe_relative_target(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_ALLOWED_ORIGINS", "https://preview.example")

    assert sanitize_redirect_to("/agents/thread-1/plan?from=slack#review") == (
        "https://dashboard.example/agents/thread-1/plan?from=slack#review"
    )


def test_sanitize_redirect_to_rejects_external_target(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_ALLOWED_ORIGINS", "https://preview.example")

    assert sanitize_redirect_to("https://evil.example/agents/thread-1/plan") == (
        "https://dashboard.example"
    )


def test_sanitize_redirect_to_rejects_unsafe_targets(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_ALLOWED_ORIGINS", "https://preview.example")

    for target in (
        "//evil.example/agents/thread-1/plan",
        "/login?redirect=/agents/thread-1/plan",
        "/dashboard/api/auth/callback",
        "https://dashboard.example/dashboard/api/auth/callback",
    ):
        assert sanitize_redirect_to(target) == "https://dashboard.example"


def test_login_hands_every_sign_in_to_langsmith(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret")

    app = FastAPI()
    app.include_router(routes.router)
    params = {"desktop": "true", "desktop_handoff": "a" * 43, "desktop_port": "51234"}
    with TestClient(app) as client:
        response = client.get("/dashboard/api/auth/login", params=params, follow_redirects=False)

    location = urlparse(response.headers["location"])
    assert location.path == "/dashboard/api/auth/langsmith/login"
    assert parse_qs(location.query) == {key: [value] for key, value in params.items()}


_VERIFIER = "desktop-verifier"
_CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(_VERIFIER.encode()).digest()).decode().rstrip("=")
)
_TOKENS = {"access_token": "gho_test", "refresh_token": "ghr_test", "expires_in": 3600}


@pytest.fixture
def linking(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A signed-in LangSmith person whose GitHub legs are stubbed; collects what linking does."""
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret")
    monkeypatch.setenv("GITHUB_APP_CLIENT_ID", "client-id")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    collected: dict[str, Any] = {}
    person = User()
    person.identities.append(UserIdentity(provider="langsmith", external_id="ls-sub"))
    joined = User()
    joined.identities.append(UserIdentity(provider="langsmith", external_id="ls-sub"))
    joined.identities.append(UserIdentity(provider="github", external_id="42", login="alice"))

    async def fake_exchange_code(code: str) -> dict[str, Any]:
        assert code == "oauth-code"
        return _TOKENS

    async def fake_fetch_github_user(access_token: str) -> tuple[GithubUser, str | None]:
        assert access_token == "gho_test"
        return GithubUser(id=42, login="alice"), "alice@example.com"

    async def fake_enforce_github_login_gate(login: str) -> None:
        assert login == "alice"

    async def fake_upsert(login: str, email: str, data: dict[str, Any]) -> None:
        collected["tokens"] = {"login": login, "email": email, "data": data}

    async def fake_join(self: User, external_id: str, *, login: str, email: str) -> User:
        collected["join"] = {"user": self, "external_id": external_id, "login": login}
        return joined

    async def fake_user(self: DashboardPrincipal) -> User:
        return joined if self.github_login else person

    monkeypatch.setattr(github_link, "exchange_code", fake_exchange_code)
    monkeypatch.setattr(github_link, "fetch_github_user", fake_fetch_github_user)
    monkeypatch.setattr(github_link, "enforce_github_login_gate", fake_enforce_github_login_gate)
    monkeypatch.setattr(github_link, "upsert_access_token_from_github_response", fake_upsert)
    monkeypatch.setattr(User, "join_github", fake_join)
    monkeypatch.setattr(DashboardPrincipal, "user", fake_user)
    monkeypatch.setattr(DashboardPrincipal, "authorize", AsyncMock())
    collected["person"] = person
    collected["joined"] = joined
    return collected


def _langsmith_client(person: User) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app, base_url="https://dashboard.example")
    client.cookies.set(
        COOKIE_NAME,
        issue_session(
            login=f"user:{person.id}",
            email="alice@example.com",
            avatar_url=None,
            user_id=str(person.id),
            provider="langsmith",
            langsmith_subject="ls-sub",
        ),
    )
    return client


def test_linking_github_turns_the_langsmith_session_into_its_github_login(
    linking: dict[str, Any],
) -> None:
    target = "/agents?from=slack"
    with _langsmith_client(linking["person"]) as client:
        start = client.get(
            "/dashboard/api/github/login", params={"redirect_to": target}, follow_redirects=False
        )
        authorize = parse_qs(urlparse(start.headers["location"]).query)
        assert authorize["redirect_uri"] == [
            "https://dashboard.example/dashboard/api/auth/callback"
        ]
        callback = client.get(
            "/dashboard/api/auth/callback",
            params={"code": "oauth-code", "state": authorize["state"][0]},
            follow_redirects=False,
        )

        assert callback.status_code == 302
        assert callback.headers["location"] == f"https://dashboard.example{target}"
        session = decode_session(callback.cookies[COOKIE_NAME])
        assert (session["sub"], session["provider"]) == ("alice", "langsmith")
        assert session["user_id"] == str(linking["joined"].id)

    assert linking["join"] == {"user": linking["person"], "external_id": "42", "login": "alice"}
    assert linking["tokens"] == {"login": "alice", "email": "alice@example.com", "data": _TOKENS}


def test_linking_github_rejects_a_callback_without_its_state_cookie(
    linking: dict[str, Any],
) -> None:
    with _langsmith_client(linking["person"]) as client:
        start = client.get("/dashboard/api/github/login", follow_redirects=False)
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    with _langsmith_client(linking["person"]) as client:
        callback = client.get(
            "/dashboard/api/auth/callback",
            params={"code": "oauth-code", "state": state},
            follow_redirects=False,
        )

    assert callback.status_code == 400
    assert "oauth state mismatch" in callback.json()["detail"]
    assert "join" not in linking


def test_desktop_github_link_hands_back_only_encrypted_tokens(linking: dict[str, Any]) -> None:
    with _langsmith_client(linking["person"]) as app_client:
        start = app_client.get(
            "/dashboard/api/github/login",
            params={"desktop_handoff": _CHALLENGE, "desktop_port": 51234},
            follow_redirects=False,
        )
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

        app = FastAPI()
        app.include_router(routes.router)
        with TestClient(app, base_url="https://dashboard.example") as browser:
            callback = browser.get(
                "/dashboard/api/auth/callback",
                params={"code": "oauth-code", "state": state},
                follow_redirects=False,
            )
        location = urlparse(callback.headers["location"])
        assert (location.netloc, location.path) == ("127.0.0.1:51234", "/callback")
        handoff = parse_qs(location.query)["code"][0]
        assert "gho_test" not in str(jwt.decode(handoff, "test-secret", algorithms=["HS256"]))
        assert "join" not in linking

        exchange = app_client.post(
            "/dashboard/api/github/desktop/exchange",
            json={"code": handoff, "verifier": _VERIFIER},
            headers={"origin": "open-swe://app"},
        )

    assert exchange.status_code == 200
    assert linking["join"]["login"] == "alice"
    assert linking["tokens"]["data"] == _TOKENS


@pytest.mark.parametrize(
    "query",
    [
        {"code": "github-code", "state": "mcp-transaction"},
        {"error": "access_denied", "state": "mcp-transaction"},
    ],
)
def test_auth_callback_hands_mcp_sign_ins_to_the_mcp_server(query: dict[str, str]) -> None:
    app = FastAPI()
    app.include_router(routes.router)

    with TestClient(app) as client:
        response = client.get("/dashboard/api/auth/callback", params=query, follow_redirects=False)

    assert response.status_code == 302
    location = urlparse(response.headers["location"])
    assert location.path == "/dashboard/api/auth/mcp/callback"
    assert parse_qs(location.query) == {key: [value] for key, value in query.items()}
