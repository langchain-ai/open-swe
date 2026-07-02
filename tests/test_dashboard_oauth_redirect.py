from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.dashboard import routes
from agent.dashboard.oauth import COOKIE_NAME, sanitize_redirect_to


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
        "/agents/thread-1/plan?from=slack#review"
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


def test_auth_callback_preserves_relative_plan_redirect(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://testserver")
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "http://testserver")
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret")
    monkeypatch.setenv("GITHUB_APP_CLIENT_ID", "client-id")

    token_data = {"access_token": "gho_test", "refresh_token": "ghr_test", "expires_in": 3600}
    persisted: dict[str, Any] = {}

    async def fake_exchange_code(code: str) -> dict[str, Any]:
        assert code == "oauth-code"
        return token_data

    async def fake_fetch_github_user(access_token: str) -> tuple[dict[str, Any], str | None]:
        assert access_token == "gho_test"
        return {
            "login": "alice",
            "avatar_url": "https://avatars.example/alice.png",
        }, "alice@example.com"

    async def fake_enforce_org_login_gate(login: str) -> None:
        assert login == "alice"

    async def fake_upsert_access_token_from_github_response(
        login: str, email: str, data: dict[str, Any]
    ) -> None:
        persisted.update({"login": login, "email": email, "data": data})

    monkeypatch.setattr(routes, "exchange_code", fake_exchange_code)
    monkeypatch.setattr(routes, "fetch_github_user", fake_fetch_github_user)
    monkeypatch.setattr(routes, "enforce_org_login_gate", fake_enforce_org_login_gate)
    monkeypatch.setattr(
        routes,
        "upsert_access_token_from_github_response",
        fake_upsert_access_token_from_github_response,
    )

    app = FastAPI()
    app.include_router(routes.router)
    target = "/agents/thread-1/plan?from=slack#review"

    with TestClient(app) as client:
        login_response = client.get(
            "/dashboard/api/auth/login", params={"redirect_to": target}, follow_redirects=False
        )
        assert login_response.status_code == 302
        state = parse_qs(urlparse(login_response.headers["location"]).query)["state"][0]

        callback_response = client.get(
            "/dashboard/api/auth/callback",
            params={"code": "oauth-code", "state": state},
            follow_redirects=False,
        )

        assert callback_response.status_code == 302
        assert callback_response.headers["location"] == target
        assert client.cookies.get(COOKIE_NAME)

    assert persisted == {"login": "alice", "email": "alice@example.com", "data": token_data}
