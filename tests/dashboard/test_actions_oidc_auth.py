"""Admin auth for GitHub Actions workflows via OIDC."""

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from agent.api import oidc_auth
from agent.api.oidc_auth import (
    DEFAULT_OIDC_AUDIENCE,
    GITHUB_ACTIONS_ISSUER,
    actions_oidc_configured,
    admin_session_for_actions_oidc,
    is_actions_oidc_token,
)

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _token(
    *,
    sub: str = "repo:acme/images:ref:refs/heads/main",
    repository: str | None = "acme/images",
    audience: str = "open-swe",
    issuer: str = GITHUB_ACTIONS_ISSUER,
    expired: bool = False,
    key: Any = _KEY,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": sub,
        "iat": now - 60,
        "exp": now - 10 if expired else now + 300,
    }
    if repository is not None:
        claims["repository"] = repository
    return jwt.encode(claims, key, algorithm="RS256")


class _FakeJWKClient:
    """Stands in for GitHub's JWKS endpoint, serving the local test key."""

    def get_signing_key_from_jwt(self, token: str) -> Any:
        return type("PyJWK", (), {"key": _KEY.public_key()})()


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oidc_auth, "_client", _FakeJWKClient)


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADMIN_OIDC_AUDIENCE", raising=False)
    monkeypatch.setenv("ADMIN_OIDC_SUBJECTS", "acme/images")


def test_is_actions_oidc_token_routes_only_actions_jwts() -> None:
    assert is_actions_oidc_token(_token())
    # Routing must not depend on validity, or a stale token would be sent to
    # GitHub as if it were a user token and fail with an unrelated error.
    assert is_actions_oidc_token(_token(expired=True))
    assert not is_actions_oidc_token(_token(issuer="https://evil.example"))
    assert not is_actions_oidc_token("github_pat_abc123")


def test_not_configured_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADMIN_OIDC_AUDIENCE", raising=False)
    monkeypatch.delenv("ADMIN_OIDC_SUBJECTS", raising=False)

    assert not actions_oidc_configured()


def test_audience_without_subjects_stays_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_OIDC_AUDIENCE", "custom-aud")
    monkeypatch.delenv("ADMIN_OIDC_SUBJECTS", raising=False)

    assert not actions_oidc_configured()


async def test_audience_defaults_to_open_swe(configured: None) -> None:
    """`configured` leaves ADMIN_OIDC_AUDIENCE unset."""
    session = await admin_session_for_actions_oidc(_token(audience=DEFAULT_OIDC_AUDIENCE))

    assert session["sub"] == "actions:acme/images"


async def test_audience_override_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_OIDC_SUBJECTS", "acme/images")
    monkeypatch.setenv("ADMIN_OIDC_AUDIENCE", "custom-aud")

    session = await admin_session_for_actions_oidc(_token(audience="custom-aud"))
    assert session["sub"] == "actions:acme/images"

    with pytest.raises(HTTPException) as exc:
        await admin_session_for_actions_oidc(_token(audience=DEFAULT_OIDC_AUDIENCE))
    assert exc.value.status_code == 401


async def test_rejects_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADMIN_OIDC_AUDIENCE", raising=False)
    monkeypatch.delenv("ADMIN_OIDC_SUBJECTS", raising=False)

    with pytest.raises(HTTPException) as exc:
        await admin_session_for_actions_oidc(_token())

    assert exc.value.status_code == 401


async def test_accepts_allowlisted_repository(configured: None) -> None:
    session = await admin_session_for_actions_oidc(_token())

    assert session["sub"] == "actions:acme/images"
    assert session["auth"] == "actions_oidc"
    assert session["oidc_subject"] == "repo:acme/images:ref:refs/heads/main"


async def test_accepts_exact_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_OIDC_AUDIENCE", "open-swe")
    monkeypatch.setenv("ADMIN_OIDC_SUBJECTS", "repo:acme/images:ref:refs/heads/main")

    session = await admin_session_for_actions_oidc(_token())
    assert session["sub"] == "actions:acme/images"

    with pytest.raises(HTTPException) as exc:
        await admin_session_for_actions_oidc(_token(sub="repo:acme/images:ref:refs/heads/feature"))
    assert exc.value.status_code == 403


async def test_rejects_other_repository(configured: None) -> None:
    with pytest.raises(HTTPException) as exc:
        await admin_session_for_actions_oidc(
            _token(sub="repo:evil/images:ref:refs/heads/main", repository="evil/images")
        )

    assert exc.value.status_code == 403


async def test_rejects_wrong_audience(configured: None) -> None:
    with pytest.raises(HTTPException) as exc:
        await admin_session_for_actions_oidc(_token(audience="other-service"))

    assert exc.value.status_code == 401


async def test_rejects_expired_token(configured: None) -> None:
    with pytest.raises(HTTPException) as exc:
        await admin_session_for_actions_oidc(_token(expired=True))

    assert exc.value.status_code == 401


async def test_rejects_foreign_signature(configured: None) -> None:
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    with pytest.raises(HTTPException) as exc:
        await admin_session_for_actions_oidc(_token(key=other_key))

    assert exc.value.status_code == 401
