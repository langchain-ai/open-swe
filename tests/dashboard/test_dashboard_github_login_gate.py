"""Tests for the dashboard GitHub login allowlists."""

from unittest.mock import AsyncMock, call

import pytest
from fastapi import HTTPException

from agent.dashboard import oauth


@pytest.mark.parametrize("value", [None, "  ,  "])
def test_startup_rejects_missing_allowlists(monkeypatch, caplog, value: str | None) -> None:
    monkeypatch.delenv("OPEN_SWE_LOCAL_AUTH_TOKEN", raising=False)
    if value is None:
        monkeypatch.delenv("ALLOWED_GITHUB_ORGS", raising=False)
        monkeypatch.delenv("ALLOWED_GITHUB_USERS", raising=False)
    else:
        monkeypatch.setenv("ALLOWED_GITHUB_ORGS", value)
        monkeypatch.setenv("ALLOWED_GITHUB_USERS", value)

    with pytest.raises(
        RuntimeError, match="ALLOWED_GITHUB_ORGS or ALLOWED_GITHUB_USERS must be configured"
    ):
        oauth.validate_github_login_allowlist()

    assert "ALLOWED_GITHUB_ORGS or ALLOWED_GITHUB_USERS must be configured" in caplog.text


@pytest.mark.asyncio
async def test_app_lifespan_exits_when_allowlists_are_missing(monkeypatch) -> None:
    from agent.api.app import app, lifespan

    monkeypatch.delenv("ALLOWED_GITHUB_ORGS", raising=False)
    monkeypatch.delenv("ALLOWED_GITHUB_USERS", raising=False)
    monkeypatch.delenv("OPEN_SWE_LOCAL_AUTH_TOKEN", raising=False)

    with pytest.raises(
        RuntimeError, match="ALLOWED_GITHUB_ORGS or ALLOWED_GITHUB_USERS must be configured"
    ):
        async with lifespan(app):
            pass


@pytest.mark.parametrize("variable", ["ALLOWED_GITHUB_ORGS", "ALLOWED_GITHUB_USERS"])
def test_startup_accepts_either_allowlist(monkeypatch, variable: str) -> None:
    monkeypatch.delenv("ALLOWED_GITHUB_ORGS", raising=False)
    monkeypatch.delenv("ALLOWED_GITHUB_USERS", raising=False)
    monkeypatch.setenv(variable, "allowed")

    oauth.validate_github_login_allowlist()


def test_desktop_local_backend_accepts_missing_allowlists(monkeypatch) -> None:
    monkeypatch.delenv("ALLOWED_GITHUB_ORGS", raising=False)
    monkeypatch.delenv("ALLOWED_GITHUB_USERS", raising=False)
    monkeypatch.setenv("OPEN_SWE_LOCAL_AUTH_TOKEN", "local-token")

    oauth.validate_github_login_allowlist()


@pytest.mark.asyncio
async def test_gate_allows_explicit_user_case_insensitively(monkeypatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", " Alice, bob ")
    monkeypatch.delenv("ALLOWED_GITHUB_ORGS", raising=False)
    membership = AsyncMock()
    monkeypatch.setattr(oauth, "is_user_active_org_member", membership)

    await oauth.enforce_github_login_gate("ALICE")

    membership.assert_not_awaited()


@pytest.mark.asyncio
async def test_gate_allows_member_of_any_configured_org(monkeypatch) -> None:
    monkeypatch.delenv("ALLOWED_GITHUB_USERS", raising=False)
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "primary, secondary")
    membership = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(oauth, "is_user_active_org_member", membership)

    await oauth.enforce_github_login_gate("Insider")

    assert membership.await_args_list == [call("insider", "primary"), call("insider", "secondary")]


@pytest.mark.asyncio
@pytest.mark.parametrize("authorized_by", ["user", "org"])
async def test_gate_uses_union_when_both_allowlists_are_set(
    monkeypatch, authorized_by: str
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "primary")
    membership = AsyncMock(return_value=authorized_by == "org")
    monkeypatch.setattr(oauth, "is_user_active_org_member", membership)

    login = "alice" if authorized_by == "user" else "insider"
    await oauth.enforce_github_login_gate(login)

    if authorized_by == "user":
        membership.assert_not_awaited()
    else:
        membership.assert_awaited_once_with("insider", "primary")


@pytest.mark.asyncio
async def test_gate_rejects_user_outside_both_allowlists(monkeypatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "primary")
    monkeypatch.setattr(oauth, "is_user_active_org_member", AsyncMock(return_value=False))

    with pytest.raises(HTTPException) as exc:
        await oauth.enforce_github_login_gate("stranger")

    assert exc.value.status_code == 403
    assert exc.value.detail == "your GitHub account is not authorized"
