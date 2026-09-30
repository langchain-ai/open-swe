"""How the CLI authenticates: the dashboard's own cookie, from its own origin."""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from agent.dashboard import oauth

BACKEND = "https://backend.example"


def _request(
    *,
    method: str = "POST",
    cookie: str | None = None,
    origin: str | None = None,
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if cookie is not None:
        headers.append((b"cookie", cookie.encode()))
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    return Request(
        {
            "type": "http",
            "scheme": "https",
            "server": ("backend.example", 443),
            "method": method,
            "path": "/dashboard/api/bridges",
            "headers": headers,
        }
    )


@pytest.fixture(autouse=True)
def _dashboard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret")
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")


def _cookie(login: str = "test-user") -> str:
    token = oauth.issue_session(
        login=login, email=f"{login}@example.com", avatar_url=None, user_id="u-1"
    )
    return f"{oauth.COOKIE_NAME}={token}"


def test_the_session_cookie_names_the_caller() -> None:
    assert oauth.require_session(_request(cookie=_cookie()))["sub"] == "test-user"


def test_a_mutation_from_the_backends_own_origin_is_allowed() -> None:
    """What the CLI sends: the cookie, and the origin of the API it is calling.

    The dashboard is not always served from the backend, so this origin is not
    in the configured allowlist; it passes because a request to an origin is
    never cross-site with respect to that same origin.
    """
    oauth.require_same_origin_for_mutations(_request(cookie=_cookie(), origin=BACKEND))


def test_a_mutation_from_somewhere_else_is_refused() -> None:
    with pytest.raises(HTTPException) as elsewhere:
        oauth.require_same_origin_for_mutations(
            _request(cookie=_cookie(), origin="https://attacker.example")
        )
    with pytest.raises(HTTPException) as nameless:
        oauth.require_same_origin_for_mutations(_request(cookie=_cookie()))

    assert elsewhere.value.status_code == 403
    assert nameless.value.status_code == 403


def test_an_unauthenticated_request_is_refused() -> None:
    with pytest.raises(HTTPException) as absent:
        oauth.require_session(_request())

    assert absent.value.status_code == 401
