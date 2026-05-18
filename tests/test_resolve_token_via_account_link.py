"""Webhook resolution preferring an explicit account link over LangSmith email."""

from __future__ import annotations

from typing import Any

import pytest

from agent.utils import auth as auth_module


@pytest.fixture
def fake_persist(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def fake_persist_encrypted_github_token(
        thread_id: str, token: str, expires_at: str | None = None
    ) -> str:
        captured["thread_id"] = thread_id
        captured["token"] = token
        captured["expires_at"] = expires_at
        return "encrypted-bytes"

    monkeypatch.setattr(
        auth_module, "persist_encrypted_github_token", fake_persist_encrypted_github_token
    )
    return captured


async def test_resolves_via_slack_link(
    monkeypatch: pytest.MonkeyPatch, fake_persist: dict[str, Any]
) -> None:
    async def fake_get_slack_link_by_user(slack_user_id: str) -> dict[str, Any] | None:
        if slack_user_id == "U01":
            return {"github_login": "octocat"}
        return None

    async def fake_get_access_token(login: str) -> str | None:
        return "gho_user_token" if login == "octocat" else None

    monkeypatch.setattr(auth_module, "get_slack_link_by_user", fake_get_slack_link_by_user)
    monkeypatch.setattr(auth_module, "get_access_token", fake_get_access_token)

    result = await auth_module._resolve_token_via_account_link(
        {"source": "slack", "slack_thread": {"triggering_user_id": "U01"}},
        thread_id="t1",
    )
    assert result is not None
    token, encrypted, expires_at = result
    assert token == "gho_user_token"
    assert encrypted == "encrypted-bytes"
    assert expires_at is None
    assert fake_persist["token"] == "gho_user_token"


async def test_returns_none_when_no_link(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_slack_link_by_user(slack_user_id: str) -> dict[str, Any] | None:
        return None

    monkeypatch.setattr(auth_module, "get_slack_link_by_user", fake_get_slack_link_by_user)
    result = await auth_module._resolve_token_via_account_link(
        {"source": "slack", "slack_thread": {"triggering_user_id": "U_UNKNOWN"}},
        thread_id="t1",
    )
    assert result is None


async def test_returns_none_when_link_has_no_stored_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_slack_link_by_user(slack_user_id: str) -> dict[str, Any] | None:
        return {"github_login": "ghost"}

    async def fake_get_access_token(login: str) -> str | None:
        return None

    monkeypatch.setattr(auth_module, "get_slack_link_by_user", fake_get_slack_link_by_user)
    monkeypatch.setattr(auth_module, "get_access_token", fake_get_access_token)

    result = await auth_module._resolve_token_via_account_link(
        {"source": "slack", "slack_thread": {"triggering_user_id": "U01"}},
        thread_id="t1",
    )
    assert result is None


async def test_resolves_via_linear_link(
    monkeypatch: pytest.MonkeyPatch, fake_persist: dict[str, Any]
) -> None:
    async def fake_get_linear_link_by_user(linear_user_id: str) -> dict[str, Any] | None:
        if linear_user_id == "lin-42":
            return {"github_login": "octocat"}
        return None

    async def fake_get_access_token(login: str) -> str | None:
        return "gho_linear_user"

    monkeypatch.setattr(auth_module, "get_linear_link_by_user", fake_get_linear_link_by_user)
    monkeypatch.setattr(auth_module, "get_access_token", fake_get_access_token)

    result = await auth_module._resolve_token_via_account_link(
        {"source": "linear", "linear_issue": {"triggering_user_id": "lin-42"}},
        thread_id="t1",
    )
    assert result is not None
    assert result[0] == "gho_linear_user"


async def test_github_source_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The github source goes through its own email-map path; never via link lookup."""
    result = await auth_module._resolve_token_via_account_link(
        {"source": "github", "github_login": "octocat"},
        thread_id="t1",
    )
    assert result is None
