"""How a client with no cookie jar authenticates against the dashboard API."""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from agent.dashboard import oauth


def _request(*, authorization: str | None = None, cookie: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    if cookie is not None:
        headers.append((b"cookie", cookie.encode()))
    return Request(
        {
            "type": "http",
            "scheme": "https",
            "server": ("backend.example", 443),
            "method": "GET",
            "path": "/dashboard/api/bridges/abc/requests",
            "headers": headers,
        }
    )


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret")


def _session_token(login: str = "test-user") -> str:
    return oauth.issue_session(
        login=login, email=f"{login}@example.com", avatar_url=None, user_id="u-1"
    )


def test_a_bearer_session_authenticates_like_the_cookie() -> None:
    token = _session_token()

    from_header = oauth.require_session(_request(authorization=f"Bearer {token}"))
    from_cookie = oauth.require_session(_request(cookie=f"{oauth.COOKIE_NAME}={token}"))

    assert from_header["sub"] == "test-user"
    assert from_cookie == from_header


def test_the_cookie_wins_over_a_bearer_header() -> None:
    cookie_token = _session_token("cookie-user")

    session = oauth.require_session(
        _request(
            authorization=f"Bearer {_session_token('header-user')}",
            cookie=f"{oauth.COOKIE_NAME}={cookie_token}",
        )
    )

    assert session["sub"] == "cookie-user"


def test_a_bearer_that_is_not_a_session_is_refused() -> None:
    with pytest.raises(HTTPException) as unusable:
        oauth.require_session(_request(authorization="Bearer gho_not-a-session"))
    with pytest.raises(HTTPException) as absent:
        oauth.require_session(_request())

    assert unusable.value.status_code == 401
    assert absent.value.status_code == 401
