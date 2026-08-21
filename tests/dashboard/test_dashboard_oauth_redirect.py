import base64
import hashlib
from typing import Any
from urllib.parse import parse_qs, urlparse

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.dashboard import routes
from agent.dashboard.github_token_auth import GithubIdentity
from agent.dashboard.oauth import COOKIE_NAME, decode_session, sanitize_redirect_to
from agent.dashboard.routes import auth as auth_routes


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


def test_desktop_login_uses_the_requested_backend_callback(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret")
    monkeypatch.setenv("GITHUB_APP_CLIENT_ID", "client-id")

    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app, base_url="http://backend.example") as client:
        response = client.get(
            "/dashboard/api/auth/login",
            params={"desktop": "true"},
            headers={"x-forwarded-proto": "https"},
            follow_redirects=False,
        )

    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["redirect_uri"] == ["https://backend.example/dashboard/api/auth/callback"]


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

    async def fake_fetch_github_identity(access_token: str) -> GithubIdentity:
        assert access_token == "gho_test"
        return GithubIdentity(
            login="alice",
            email="alice@example.com",
            avatar_url="https://avatars.example/alice.png",
        )

    async def fake_enforce_org_login_gate(login: str) -> None:
        assert login == "alice"

    async def fake_upsert_access_token_from_github_response(
        login: str, email: str, data: dict[str, Any]
    ) -> None:
        persisted.update({"login": login, "email": email, "data": data})

    monkeypatch.setattr(auth_routes, "exchange_code", fake_exchange_code)
    monkeypatch.setattr(auth_routes, "fetch_github_identity", fake_fetch_github_identity)
    monkeypatch.setattr(auth_routes, "enforce_org_login_gate", fake_enforce_org_login_gate)
    monkeypatch.setattr(
        auth_routes,
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
        assert callback_response.headers["location"] == f"http://testserver{target}"
        assert client.cookies.get(COOKIE_NAME)

    assert persisted == {"login": "alice", "email": "alice@example.com", "data": token_data}


def test_auth_callback_cross_origin_redirect(monkeypatch) -> None:
    """Relative redirect_to is expanded with DASHBOARD_BASE_URL so the browser
    lands on the dashboard origin, not the API origin, in cross-origin setups."""
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://localhost:3000")
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "http://localhost:2024")
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret")
    monkeypatch.setenv("GITHUB_APP_CLIENT_ID", "client-id")

    token_data = {"access_token": "gho_test", "refresh_token": "ghr_test", "expires_in": 3600}
    persisted: dict[str, Any] = {}

    async def fake_exchange_code(code: str) -> dict[str, Any]:
        return token_data

    async def fake_fetch_github_identity(access_token: str) -> GithubIdentity:
        return GithubIdentity(
            login="alice", email=None, avatar_url="https://avatars.example/alice.png"
        )

    async def fake_enforce_org_login_gate(login: str) -> None:
        pass

    async def fake_upsert_access_token_from_github_response(
        login: str, email: str, data: dict[str, Any]
    ) -> None:
        persisted.update({"login": login, "email": email, "data": data})

    monkeypatch.setattr(auth_routes, "exchange_code", fake_exchange_code)
    monkeypatch.setattr(auth_routes, "fetch_github_identity", fake_fetch_github_identity)
    monkeypatch.setattr(auth_routes, "enforce_org_login_gate", fake_enforce_org_login_gate)
    monkeypatch.setattr(
        auth_routes,
        "upsert_access_token_from_github_response",
        fake_upsert_access_token_from_github_response,
    )

    app = FastAPI()
    app.include_router(routes.router)
    target = "/agents/thread-1/plan?from=slack#review"

    with TestClient(app, base_url="http://localhost:2024") as client:
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
        assert callback_response.headers["location"] == f"http://localhost:3000{target}"
        assert not callback_response.headers["location"].startswith("http://localhost:2024")


def _desktop_login_env(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret")
    monkeypatch.setenv("GITHUB_APP_CLIENT_ID", "client-id")

    async def fake_exchange_code(code: str) -> dict[str, Any]:
        return {"access_token": "gho_test"}

    async def fake_fetch_github_identity(access_token: str) -> GithubIdentity:
        return GithubIdentity(login="alice", email="alice@example.com", avatar_url=None)

    async def fake_enforce_org_login_gate(login: str) -> None:
        pass

    async def fake_upsert_access_token_from_github_response(
        login: str, email: str, data: dict[str, Any]
    ) -> None:
        pass

    monkeypatch.setattr(auth_routes, "exchange_code", fake_exchange_code)
    monkeypatch.setattr(auth_routes, "fetch_github_identity", fake_fetch_github_identity)
    monkeypatch.setattr(auth_routes, "enforce_org_login_gate", fake_enforce_org_login_gate)
    monkeypatch.setattr(
        auth_routes,
        "upsert_access_token_from_github_response",
        fake_upsert_access_token_from_github_response,
    )


def test_desktop_login_hands_the_session_back_over_loopback(monkeypatch) -> None:
    _desktop_login_env(monkeypatch)
    verifier = "desktop-verifier"
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )

    app = FastAPI()
    app.include_router(routes.router)
    # Same host as DASHBOARD_API_BASE_URL, because that is the only arrangement a
    # real browser can complete: it carries the state cookie from login to
    # callback, and the cookie is bound to the origin that set it.
    with TestClient(app, base_url="https://dashboard.example") as client:
        login_response = client.get(
            "/dashboard/api/auth/login",
            params={"desktop_handoff": challenge, "desktop_port": 51234},
            follow_redirects=False,
        )
        authorize = parse_qs(urlparse(login_response.headers["location"]).query)
        assert authorize["redirect_uri"] == [
            "https://dashboard.example/dashboard/api/auth/callback"
        ]
        state = authorize["state"][0]

        callback_response = client.get(
            "/dashboard/api/auth/callback",
            params={"code": "oauth-code", "state": state},
            follow_redirects=False,
        )
        assert callback_response.status_code == 302
        location = urlparse(callback_response.headers["location"])
        assert (location.scheme, location.netloc, location.path) == (
            "http",
            "127.0.0.1:51234",
            "/callback",
        )
        # The browser is only a courier here — it must not keep a session.
        assert not callback_response.cookies.get(COOKIE_NAME)

        handoff = parse_qs(location.query)["code"][0]
        exchange = client.post(
            "/dashboard/api/auth/desktop/exchange",
            json={"code": handoff, "verifier": verifier},
            headers={"origin": "open-swe://app"},
        )
        assert exchange.status_code == 200
        assert decode_session(exchange.json()["session"])["sub"] == "alice"

        forged = client.post(
            "/dashboard/api/auth/desktop/exchange",
            json={"code": handoff, "verifier": "wrong-verifier"},
            headers={"origin": "open-swe://app"},
        )
        assert forged.status_code == 400


def test_desktop_login_rejects_a_malformed_handoff_challenge(monkeypatch) -> None:
    _desktop_login_env(monkeypatch)

    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app, base_url="https://dashboard.example") as client:
        response = client.get(
            "/dashboard/api/auth/login",
            params={"desktop_handoff": "../evil", "desktop_port": 51234},
            follow_redirects=False,
        )
        assert response.status_code == 400

        out_of_range = client.get(
            "/dashboard/api/auth/login",
            params={"desktop_handoff": "a" * 43, "desktop_port": 80},
            follow_redirects=False,
        )
        assert out_of_range.status_code == 422


def test_desktop_handoff_code_carries_no_session(monkeypatch) -> None:
    """The handoff code rides in a URL the browser records and extensions can read.

    A JWT is signed, not encrypted, so anything in its payload is readable
    without the verifier — a session in there would make the PKCE check moot.
    """
    _desktop_login_env(monkeypatch)
    verifier = "desktop-verifier"
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )

    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app, base_url="https://dashboard.example") as client:
        login_response = client.get(
            "/dashboard/api/auth/login",
            params={"desktop_handoff": challenge, "desktop_port": 51234},
            follow_redirects=False,
        )
        state = parse_qs(urlparse(login_response.headers["location"]).query)["state"][0]
        callback_response = client.get(
            "/dashboard/api/auth/callback",
            params={"code": "oauth-code", "state": state},
            follow_redirects=False,
        )
        location = urlparse(callback_response.headers["location"])
        handoff = parse_qs(location.query)["code"][0]

    payload = jwt.decode(handoff, "test-secret", algorithms=["HS256"])
    assert "session" not in payload
    assert not any(
        isinstance(v, str) and v.count(".") == 2 and len(v) > 60 for v in payload.values()
    ), f"handoff payload looks like it embeds a token: {payload}"
    assert payload["sub"] == "alice"


def test_desktop_login_callback_follows_the_configured_api_origin(monkeypatch) -> None:
    """`redirect_uri` comes from DASHBOARD_API_BASE_URL, not the request host.

    Driven from a host that differs from the configured one so the two cannot
    be confused; the companion test below covers what that costs when a
    deployment sets the variable to an origin the browser never visits.
    """
    _desktop_login_env(monkeypatch)

    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app, base_url="https://upstream.langgraph.example") as client:
        response = client.get(
            "/dashboard/api/auth/login",
            params={"desktop_handoff": "a" * 43, "desktop_port": 51234},
            follow_redirects=False,
        )

    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["redirect_uri"] == ["https://dashboard.example/dashboard/api/auth/callback"]


def test_auth_callback_rejects_a_callback_on_a_different_origin(monkeypatch) -> None:
    """A callback that lands somewhere other than where login started fails.

    The state nonce lives in a cookie bound to the origin that issued it, so a
    deployment whose DASHBOARD_API_BASE_URL names a host the browser never
    visited during login cannot complete sign-in — it dies here rather than
    somewhere subtler.
    """
    _desktop_login_env(monkeypatch)

    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app, base_url="https://upstream.langgraph.example") as login_client:
        login_response = login_client.get(
            "/dashboard/api/auth/login",
            params={"desktop_handoff": "a" * 43, "desktop_port": 51234},
            follow_redirects=False,
        )
        state = parse_qs(urlparse(login_response.headers["location"]).query)["state"][0]

    # A different origin: a fresh client holds none of the login's cookies.
    with TestClient(app, base_url="https://dashboard.example") as callback_client:
        callback_response = callback_client.get(
            "/dashboard/api/auth/callback",
            params={"code": "oauth-code", "state": state},
            follow_redirects=False,
        )

    assert callback_response.status_code == 400
    assert "oauth state mismatch" in callback_response.json()["detail"]
