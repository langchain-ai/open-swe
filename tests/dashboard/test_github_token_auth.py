"""Bearer-token detection and its effect on the CSRF origin check."""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from agent.dashboard import oauth
from agent.github import token_auth as github_token_auth


def _request(
    *,
    method: str = "PUT",
    path: str = "/dashboard/api/settings",
    authorization: str | None = None,
    cookie: str | None = None,
) -> Request:
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
            "method": method,
            "path": path,
            "headers": headers,
        }
    )


def test_bearer_token_parsing() -> None:
    assert github_token_auth.bearer_token(_request(authorization="Bearer gh-tok")) == "gh-tok"
    assert github_token_auth.bearer_token(_request(authorization="bearer gh-tok")) == "gh-tok"
    assert github_token_auth.bearer_token(_request(authorization="Basic gh-tok")) is None
    assert github_token_auth.bearer_token(_request(authorization="Bearer  ")) is None
    assert github_token_auth.bearer_token(_request()) is None


def test_bearer_mutation_skips_csrf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")

    oauth.require_same_origin_for_mutations(_request(authorization="Bearer gh-tok"))


def test_bearer_with_session_cookie_still_enforces_csrf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")

    with pytest.raises(HTTPException) as exc:
        oauth.require_same_origin_for_mutations(
            _request(authorization="Bearer gh-tok", cookie=f"{oauth.COOKIE_NAME}=abc")
        )

    assert exc.value.status_code == 403
