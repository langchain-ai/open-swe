"""Tests for the /cli/* FastAPI routes."""

from __future__ import annotations

import importlib
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from agent import webapp
from agent.middleware import cli_auth as cli_auth_module
from agent.utils import cli_session as cli_session_module

_TEST_SECRET = "test-cli-session-secret-for-pytest-only"


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Reset auth env + caches before each test."""
    monkeypatch.setenv("CLI_SESSION_SECRET", _TEST_SECRET)
    monkeypatch.setenv("ALLOWED_GITHUB_ORG", "langchain-ai")
    monkeypatch.setenv("GITHUB_APP_CLIENT_ID", "Iv1.test")
    importlib.reload(cli_session_module)
    importlib.reload(cli_auth_module)
    importlib.reload(webapp)
    cli_auth_module._ORG_MEMBERSHIP_CACHE.clear()
    yield
    cli_auth_module._ORG_MEMBERSHIP_CACHE.clear()


def _issue_token(login: str, *, exp_offset: int = 0) -> str:
    now = int(time.time())
    payload = {
        "sub": login,
        "iat": now,
        "exp": now + 60 + exp_offset,
    }
    return jwt.encode(payload, _TEST_SECRET, algorithm="HS256")


def test_cli_config_is_public() -> None:
    client = TestClient(webapp.app)
    response = client.get("/cli/config")
    assert response.status_code == 200
    body = response.json()
    assert body["allowed_org"] == "langchain-ai"
    assert body["github_app_client_id"] == "Iv1.test"
    assert body["cli_api_version"] == 1
    assert body["supports_handoff"] is False


def test_cli_me_unauthenticated_returns_401() -> None:
    client = TestClient(webapp.app)
    response = client.get("/cli/me")
    assert response.status_code == 401


def test_cli_me_valid_member_returns_200(monkeypatch) -> None:
    async def fake_is_member(username: str, org: str) -> bool:  # noqa: ARG001
        return True

    monkeypatch.setattr(cli_auth_module, "is_user_active_org_member", fake_is_member)

    async def fake_get_identities(github_login: str):  # noqa: ARG001
        return None

    monkeypatch.setattr(webapp, "get_identities_for_github_login", fake_get_identities)

    token = _issue_token("octocat")
    client = TestClient(webapp.app)
    response = client.get("/cli/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["github_login"] == "octocat"


def test_cli_me_non_member_returns_403(monkeypatch) -> None:
    async def fake_is_member(username: str, org: str) -> bool:  # noqa: ARG001
        return False

    monkeypatch.setattr(cli_auth_module, "is_user_active_org_member", fake_is_member)

    token = _issue_token("stranger")
    client = TestClient(webapp.app)
    response = client.get("/cli/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_cli_me_expired_token_returns_401() -> None:
    now = int(time.time())
    expired = jwt.encode(
        {"sub": "octocat", "iat": now - 3600, "exp": now - 100},
        _TEST_SECRET,
        algorithm="HS256",
    )
    client = TestClient(webapp.app)
    response = client.get("/cli/me", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401


def test_cli_me_invalid_token_returns_401() -> None:
    client = TestClient(webapp.app)
    response = client.get("/cli/me", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert response.status_code == 401
