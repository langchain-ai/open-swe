from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from agent.dashboard import oauth, password_auth, routes

_TEST_SECRET = "test-secret-with-at-least-thirty-two-bytes"


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: list[str], key: str) -> dict[str, Any] | None:
        value = self.items.get((tuple(namespace), key))
        return {"value": value} if value is not None else None

    async def put_item(self, namespace: list[str], key: str, value: dict[str, Any]) -> None:
        self.items[(tuple(namespace), key)] = value

    async def search_items(
        self,
        namespace: list[str],
        filter: dict[str, Any] | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> dict[str, Any]:
        values = [
            value
            for (stored_namespace, _), value in self.items.items()
            if stored_namespace == tuple(namespace)
        ]
        if filter:
            values = [
                value
                for value in values
                if all(value.get(key) == expected for key, expected in filter.items())
            ]
        return {"items": [{"value": value} for value in values[offset : offset + limit]]}


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", _TEST_SECRET)
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://testserver")
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "http://testserver")
    client = _FakeClient()
    monkeypatch.setattr(password_auth, "_client", lambda: client)
    return client


@pytest.fixture
def dashboard_client(fake_client: _FakeClient) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


async def test_password_account_hashes_and_authenticates(fake_client: _FakeClient) -> None:
    account = await password_auth.upsert_password_account(
        login="alice",
        email="Alice@Example.com",
        password="correct horse battery staple",
        enabled=True,
    )

    stored = await password_auth.get_password_account("alice@example.com")
    assert account["email"] == "alice@example.com"
    assert stored["password_hash"].startswith("pbkdf2_sha256$")
    assert "correct horse" not in stored["password_hash"]

    authenticated = await password_auth.authenticate_password(
        "ALICE@example.com",
        "correct horse battery staple",
    )
    assert authenticated["login"] == "alice"


async def test_password_auth_rejects_failed_and_disabled_login(
    fake_client: _FakeClient,
) -> None:
    await password_auth.upsert_password_account(
        login="alice",
        email="alice@example.com",
        password="correct horse battery staple",
        enabled=True,
    )

    with pytest.raises(HTTPException) as failed:
        await password_auth.authenticate_password("alice@example.com", "wrong password")
    assert failed.value.status_code == 401

    await password_auth.set_password_account_enabled("alice@example.com", enabled=False)
    with pytest.raises(HTTPException) as disabled:
        await password_auth.authenticate_password(
            "alice@example.com",
            "correct horse battery staple",
        )
    assert disabled.value.status_code == 403


async def test_password_reset_tokens_are_hashed_single_use(
    fake_client: _FakeClient,
) -> None:
    await password_auth.upsert_password_account(
        login="alice",
        email="alice@example.com",
        password="old password with enough length",
        enabled=True,
    )

    reset = await password_auth.create_password_reset_token(
        "alice@example.com",
        requested_by="admin",
    )

    stored_tokens = [
        value
        for (namespace, _), value in fake_client.store.items.items()
        if namespace == tuple(password_auth.PASSWORD_RESET_NAMESPACE)
    ]
    assert len(stored_tokens) == 1
    assert reset["token"] not in str(stored_tokens[0])

    updated = await password_auth.reset_password(
        reset["token"],
        "new password with enough length",
    )
    assert updated["email"] == "alice@example.com"
    await password_auth.authenticate_password(
        "alice@example.com",
        "new password with enough length",
    )

    with pytest.raises(HTTPException) as reused:
        await password_auth.reset_password(
            reset["token"],
            "another password with enough length",
        )
    assert reused.value.status_code == 400


def test_decode_session_rejects_expired_password_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", _TEST_SECRET)
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "alice",
            "email": "alice@example.com",
            "avatar_url": None,
            "auth_source": "password",
            "iat": now - 3600,
            "exp": now - 1,
        },
        _TEST_SECRET,
        algorithm=oauth.JWT_ALG,
    )

    with pytest.raises(HTTPException) as exc:
        oauth.decode_session(token)
    assert exc.value.status_code == 401


def test_github_session_defaults_to_github_auth_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", _TEST_SECRET)

    token = oauth.issue_session(login="octo", email="octo@example.com", avatar_url=None)

    assert oauth.decode_session(token)["auth_source"] == "github"


def test_password_login_route_sets_existing_session_cookie(
    dashboard_client: TestClient,
    fake_client: _FakeClient,
) -> None:
    import anyio

    async def seed() -> None:
        await password_auth.upsert_password_account(
            login="alice",
            email="alice@example.com",
            password="correct horse battery staple",
            enabled=True,
        )

    anyio.run(seed)

    response = dashboard_client.post(
        "/dashboard/api/auth/password/login",
        headers={"Origin": "http://testserver"},
        json={"email": "alice@example.com", "password": "correct horse battery staple"},
    )

    assert response.status_code == 204
    assert oauth.COOKIE_NAME in response.cookies

    me = dashboard_client.get("/dashboard/api/me")
    assert me.status_code == 200
    assert me.json()["login"] == "alice"
    assert me.json()["email"] == "alice@example.com"
    assert me.json()["auth_source"] == "password"


def test_password_login_route_rejects_disabled_account(
    dashboard_client: TestClient,
    fake_client: _FakeClient,
) -> None:
    import anyio

    async def seed() -> None:
        await password_auth.upsert_password_account(
            login="alice",
            email="alice@example.com",
            password="correct horse battery staple",
            enabled=False,
        )

    anyio.run(seed)

    response = dashboard_client.post(
        "/dashboard/api/auth/password/login",
        headers={"Origin": "http://testserver"},
        json={"email": "alice@example.com", "password": "correct horse battery staple"},
    )

    assert response.status_code == 403


def test_password_reset_confirm_route_updates_password(
    dashboard_client: TestClient,
    fake_client: _FakeClient,
) -> None:
    import anyio

    async def seed() -> dict[str, str]:
        await password_auth.upsert_password_account(
            login="alice",
            email="alice@example.com",
            password="old password with enough length",
            enabled=True,
        )
        return await password_auth.create_password_reset_token(
            "alice@example.com",
            requested_by="admin",
        )

    reset = anyio.run(seed)

    response = dashboard_client.post(
        "/dashboard/api/auth/password/reset/confirm",
        headers={"Origin": "http://testserver"},
        json={"token": reset["token"], "password": "new password with enough length"},
    )

    assert response.status_code == 204
    anyio.run(
        password_auth.authenticate_password,
        "alice@example.com",
        "new password with enough length",
    )


def test_admin_password_account_creation_is_admin_only(
    dashboard_client: TestClient,
    fake_client: _FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    user_token = oauth.issue_session(
        login="user",
        email="user@example.com",
        avatar_url=None,
        auth_source="password",
    )
    dashboard_client.cookies.set(oauth.COOKIE_NAME, user_token)

    denied = dashboard_client.post(
        "/dashboard/api/admin/password-accounts",
        headers={"Origin": "http://testserver"},
        json={
            "login": "alice",
            "email": "alice@example.com",
            "password": "correct horse battery staple",
            "enabled": True,
        },
    )

    assert denied.status_code == 403

    admin_token = oauth.issue_session(
        login="admin",
        email="admin@example.com",
        avatar_url=None,
        auth_source="password",
    )
    dashboard_client.cookies.set(oauth.COOKIE_NAME, admin_token)
    created = dashboard_client.post(
        "/dashboard/api/admin/password-accounts",
        headers={"Origin": "http://testserver"},
        json={
            "login": "alice",
            "email": "alice@example.com",
            "password": "correct horse battery staple",
            "enabled": True,
        },
    )

    assert created.status_code == 200
    payload = created.json()
    assert payload["login"] == "alice"
    assert payload["email"] == "alice@example.com"
    assert payload["enabled"] is True
    assert "password_hash" not in payload
