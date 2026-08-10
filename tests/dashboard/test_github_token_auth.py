"""Admin auth for CI callers using a GitHub user token."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from agent.dashboard import github_token_auth, oauth, routes


def _request(
    *,
    method: str = "PUT",
    path: str = "/dashboard/api/sandbox-settings",
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
    assert (
        github_token_auth.bearer_github_token(_request(authorization="Bearer gh-tok")) == "gh-tok"
    )
    assert (
        github_token_auth.bearer_github_token(_request(authorization="bearer gh-tok")) == "gh-tok"
    )
    assert github_token_auth.bearer_github_token(_request(authorization="Basic gh-tok")) is None
    assert github_token_auth.bearer_github_token(_request(authorization="Bearer  ")) is None
    assert github_token_auth.bearer_github_token(_request()) is None


async def test_admin_token_accepted_for_configured_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "octo")
    with patch.object(
        github_token_auth,
        "_github_identity",
        new_callable=AsyncMock,
        return_value=("octo", None),
    ):
        session = await github_token_auth.admin_session_for_github_token("gh-tok")

    assert session["sub"] == "octo"
    assert session["auth"] == "github_token"


async def test_admin_token_rejected_for_non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "octo")
    with (
        patch.object(
            github_token_auth,
            "_github_identity",
            new_callable=AsyncMock,
            return_value=("intruder", None),
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await github_token_auth.admin_session_for_github_token("gh-tok")

    assert exc.value.status_code == 403


async def test_admin_dep_prefers_bearer_over_missing_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "octo")
    with patch.object(
        github_token_auth,
        "_github_identity",
        new_callable=AsyncMock,
        return_value=("octo", "octo@example.com"),
    ):
        session = await routes._admin_session_or_ci_token(_request(authorization="Bearer gh-tok"))

    assert session["sub"] == "octo"


async def test_admin_dep_routes_oidc_tokens_to_oidc_verifier() -> None:
    """An Actions OIDC token must never be treated as a user token."""
    with (
        patch.object(routes, "is_actions_oidc_token", return_value=True),
        patch.object(
            routes,
            "admin_session_for_actions_oidc",
            new_callable=AsyncMock,
            return_value={"sub": "actions:acme/images", "auth": "actions_oidc"},
        ) as verify_oidc,
        patch.object(github_token_auth, "_github_identity", new_callable=AsyncMock) as user_lookup,
    ):
        session = await routes._admin_session_or_ci_token(_request(authorization="Bearer oidc-jwt"))

    assert session["auth"] == "actions_oidc"
    verify_oidc.assert_awaited_once_with("oidc-jwt")
    user_lookup.assert_not_awaited()


async def test_admin_dep_requires_a_credential() -> None:
    with pytest.raises(HTTPException) as exc:
        await routes._admin_session_or_ci_token(_request())

    assert exc.value.status_code == 401


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
