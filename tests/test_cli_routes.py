"""Tests for the /cli/* FastAPI routes."""

from __future__ import annotations

import importlib
import time
from unittest.mock import AsyncMock, MagicMock

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
    monkeypatch.setenv("GITHUB_APP_CLIENT_SECRET", "secret")
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
    # Defaults to langsmith sandbox, which supports handoff.
    assert body["supports_handoff"] is True


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


def test_cli_auth_callback_uses_user_emails_when_user_email_null(monkeypatch) -> None:
    """If /user returns email=null, the callback should fall back to /user/emails."""
    monkeypatch.setattr(
        webapp, "_exchange_oauth_code", AsyncMock(return_value="user-to-server-token")
    )
    monkeypatch.setattr(
        webapp,
        "_fetch_github_user",
        AsyncMock(return_value={"login": "octocat", "email": None}),
    )
    monkeypatch.setattr(
        webapp,
        "_fetch_github_primary_email",
        AsyncMock(return_value="octocat@example.com"),
    )
    monkeypatch.setattr(webapp, "is_user_active_org_member", AsyncMock(return_value=True))
    upsert_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(webapp, "upsert_identity", upsert_mock)

    client = TestClient(webapp.app)
    response = client.get(
        "/cli/auth/callback",
        params={
            "code": "abc",
            "state": "xyz",
            "redirect_uri": "http://127.0.0.1:8765/cb",
        },
    )
    assert response.status_code == 200
    upsert_mock.assert_awaited_once()
    args, kwargs = upsert_mock.call_args
    assert args[0] == "octocat@example.com"
    assert kwargs.get("github_login") == "octocat"
    assert kwargs.get("surface") == "cli"


def test_cli_auth_callback_uses_user_email_when_present(monkeypatch) -> None:
    """If /user returns an email, no /user/emails fallback is needed."""
    monkeypatch.setattr(webapp, "_exchange_oauth_code", AsyncMock(return_value="tok"))
    monkeypatch.setattr(
        webapp,
        "_fetch_github_user",
        AsyncMock(return_value={"login": "octocat", "email": "public@example.com"}),
    )
    fallback_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(webapp, "_fetch_github_primary_email", fallback_mock)
    monkeypatch.setattr(webapp, "is_user_active_org_member", AsyncMock(return_value=True))
    upsert_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(webapp, "upsert_identity", upsert_mock)

    client = TestClient(webapp.app)
    response = client.get(
        "/cli/auth/callback",
        params={"code": "abc", "state": "xyz", "redirect_uri": "http://127.0.0.1:8765/cb"},
    )
    assert response.status_code == 200
    fallback_mock.assert_not_awaited()
    upsert_mock.assert_awaited_once()
    args, _ = upsert_mock.call_args
    assert args[0] == "public@example.com"


def test_cli_auth_callback_no_email_skips_upsert(monkeypatch) -> None:
    """If neither /user nor /user/emails returns an email, login still works."""
    monkeypatch.setattr(webapp, "_exchange_oauth_code", AsyncMock(return_value="tok"))
    monkeypatch.setattr(
        webapp,
        "_fetch_github_user",
        AsyncMock(return_value={"login": "octocat", "email": None}),
    )
    monkeypatch.setattr(webapp, "_fetch_github_primary_email", AsyncMock(return_value=None))
    monkeypatch.setattr(webapp, "is_user_active_org_member", AsyncMock(return_value=True))
    upsert_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(webapp, "upsert_identity", upsert_mock)

    client = TestClient(webapp.app)
    response = client.get(
        "/cli/auth/callback",
        params={"code": "abc", "state": "xyz", "redirect_uri": "http://127.0.0.1:8765/cb"},
    )
    assert response.status_code == 200
    upsert_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# /cli/runs/{id}/interrupt
# ---------------------------------------------------------------------------


def _mock_member_and_thread(monkeypatch, *, login: str, owner: str = "octocat") -> None:
    async def fake_is_member(username: str, org: str) -> bool:  # noqa: ARG001
        return True

    monkeypatch.setattr(cli_auth_module, "is_user_active_org_member", fake_is_member)

    async def fake_identity(github_login: str):  # noqa: ARG001
        return None

    monkeypatch.setattr(webapp, "get_identities_for_github_login", fake_identity)

    async def fake_metadata(thread_id: str):  # noqa: ARG001
        return {"cli_owner_login": owner, "github_login": owner}

    monkeypatch.setattr(webapp, "_get_thread_metadata_safe", fake_metadata)
    # Sanity: we want the test caller to be authorized for the thread.
    _ = login


def test_cli_interrupt_calls_runs_cancel(monkeypatch) -> None:
    _mock_member_and_thread(monkeypatch, login="octocat")

    fake_client = MagicMock()
    fake_client.runs = MagicMock()
    fake_client.runs.list = AsyncMock(return_value=[{"run_id": "r-1"}])
    fake_client.runs.cancel = AsyncMock(return_value=None)
    monkeypatch.setattr(webapp, "get_client", lambda url: fake_client)

    token = _issue_token("octocat")
    client = TestClient(webapp.app)
    response = client.post("/cli/runs/t-1/interrupt", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == {"interrupted": True}
    fake_client.runs.cancel.assert_awaited_once()
    args, kwargs = fake_client.runs.cancel.call_args
    assert args[0] == "t-1"
    assert args[1] == "r-1"
    assert kwargs.get("action") == "interrupt"


def test_cli_interrupt_no_active_run_returns_false(monkeypatch) -> None:
    _mock_member_and_thread(monkeypatch, login="octocat")
    fake_client = MagicMock()
    fake_client.runs = MagicMock()
    fake_client.runs.list = AsyncMock(return_value=[])
    fake_client.runs.cancel = AsyncMock(return_value=None)
    monkeypatch.setattr(webapp, "get_client", lambda url: fake_client)

    token = _issue_token("octocat")
    client = TestClient(webapp.app)
    response = client.post("/cli/runs/t-1/interrupt", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == {"interrupted": False}
    fake_client.runs.cancel.assert_not_awaited()


# ---------------------------------------------------------------------------
# Multi-user attach rejection (DESIGN.md §"intentionally does not include")
# ---------------------------------------------------------------------------


def test_concurrent_attach_rejected_with_holder_login(monkeypatch) -> None:
    _mock_member_and_thread(monkeypatch, login="octocat")

    # Claim the slot for someone else.
    webapp._CLI_STREAM_HOLDERS["t-1"] = "other-user"
    try:
        token = _issue_token("octocat")
        client = TestClient(webapp.app)
        response = client.get("/cli/runs/t-1/stream", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 409
        assert "other-user" in response.text
    finally:
        webapp._CLI_STREAM_HOLDERS.pop("t-1", None)


def test_same_user_can_reattach(monkeypatch) -> None:
    """Reattach as the same login replaces the holder rather than rejecting."""
    _mock_member_and_thread(monkeypatch, login="octocat")
    webapp._CLI_STREAM_HOLDERS["t-1"] = "octocat"
    try:
        # _claim_stream_holder returns None for same login.
        assert webapp._claim_stream_holder("t-1", "octocat") is None
        # And rejects a different login.
        assert webapp._claim_stream_holder("t-1", "other") == "octocat"
    finally:
        webapp._CLI_STREAM_HOLDERS.pop("t-1", None)
