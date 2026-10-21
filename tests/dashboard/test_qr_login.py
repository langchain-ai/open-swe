"""A QR code signs one phone in as whoever showed it, once, while it is the latest."""

import asyncio
from urllib.parse import urlparse

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from openswe.dashboard import oauth, routes
from openswe.dashboard.oauth import COOKIE_NAME, decode_session
from openswe.database import postgres
from openswe.users import User

_ORIGIN = "https://dashboard.example"


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=_ORIGIN, headers={"Origin": _ORIGIN}
    )


@pytest.fixture
async def app(registry_db: None, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("DASHBOARD_BASE_URL", _ORIGIN)
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "qr-test-secret-that-is-at-least-32-bytes")
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice")
    alice = await User.sign_in("github", "1", login="alice", email="alice@example.com")
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[oauth.require_session] = lambda: {
        "sub": "alice",
        "user_id": str(alice.id),
    }
    return app


async def _issue(app: FastAPI) -> str:
    async with _client(app) as desktop:
        response = await desktop.post("/dashboard/api/auth/qr")
    assert response.status_code == 200, response.text
    url = urlparse(response.json()["url"])
    assert url.path == "/login/qr"
    return url.fragment.removeprefix("code=")


async def _redeem(app: FastAPI, code: str) -> httpx.Response:
    async with _client(app) as phone:
        return await phone.post("/dashboard/api/auth/qr/redeem", json={"code": code})


async def test_a_code_signs_in_one_phone_and_only_the_latest_code_works(app: FastAPI) -> None:
    stale = await _issue(app)
    code = await _issue(app)

    assert (await _redeem(app, stale)).status_code == 400

    signed_in = await _redeem(app, code)
    assert signed_in.status_code == 200, signed_in.text
    assert decode_session(signed_in.cookies[COOKIE_NAME])["sub"] == "alice"

    assert (await _redeem(app, code)).status_code == 400


async def test_concurrent_redemptions_have_only_one_winner(app: FastAPI) -> None:
    code = await _issue(app)
    results = await asyncio.gather(_redeem(app, code), _redeem(app, code))
    assert sorted(response.status_code for response in results) == [200, 400]


async def test_expired_codes_and_revoked_accounts_cannot_sign_in(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = await _issue(app)
    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE qr_login_code SET expires_at = clock_timestamp() - interval '1 second'")
        )
    assert (await _redeem(app, code)).status_code == 400

    code = await _issue(app)
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "bob")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")
    assert (await _redeem(app, code)).status_code == 403


async def test_issuing_codes_requires_a_session_and_same_origin(app: FastAPI) -> None:
    async with _client(app) as client:
        response = await client.post(
            "/dashboard/api/auth/qr", headers={"Origin": "https://evil.example"}
        )
        assert response.status_code == 403
        app.dependency_overrides.clear()
        assert (await client.post("/dashboard/api/auth/qr")).status_code == 401
