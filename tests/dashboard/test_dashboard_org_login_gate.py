"""Tests for the dashboard GitHub-org login gate (ALLOWED_GITHUB_ORGS)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from agent.dashboard import oauth


def _stub_membership(monkeypatch, members: dict[str, set[str]]) -> dict[str, list[tuple[str, str]]]:
    """Stub is_user_active_org_member; ``members`` maps org -> set of logins."""
    seen: dict[str, list[tuple[str, str]]] = {"calls": []}

    async def fake_is_user_active_org_member(username: str, org: str) -> bool:
        seen["calls"].append((username, org))
        return username in members.get(org, set())

    monkeypatch.setattr(oauth, "is_user_active_org_member", fake_is_user_active_org_member)
    return seen


@pytest.mark.asyncio
async def test_gate_noop_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("ALLOWED_GITHUB_ORGS", raising=False)
    seen = _stub_membership(monkeypatch, {})

    await oauth.enforce_org_login_gate("anyone")

    assert seen["calls"] == []


@pytest.mark.asyncio
async def test_gate_noop_when_blank(monkeypatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "  ,  ")
    seen = _stub_membership(monkeypatch, {})

    await oauth.enforce_org_login_gate("anyone")

    assert seen["calls"] == []


@pytest.mark.asyncio
async def test_gate_allows_member(monkeypatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "langchain-ai")
    _stub_membership(monkeypatch, {"langchain-ai": {"insider"}})

    await oauth.enforce_org_login_gate("insider")


@pytest.mark.asyncio
async def test_gate_rejects_non_member(monkeypatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "langchain-ai")
    _stub_membership(monkeypatch, {"langchain-ai": {"insider"}})

    with pytest.raises(HTTPException) as exc:
        await oauth.enforce_org_login_gate("stranger")

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_gate_allows_member_of_any_configured_org(monkeypatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "langchain-ai, anthropics")
    _stub_membership(monkeypatch, {"anthropics": {"insider"}})

    await oauth.enforce_org_login_gate("insider")


@pytest.mark.asyncio
async def test_gate_rejects_when_member_of_no_configured_org(monkeypatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "langchain-ai,anthropics")
    seen = _stub_membership(monkeypatch, {"other-org": {"stranger"}})

    with pytest.raises(HTTPException) as exc:
        await oauth.enforce_org_login_gate("stranger")

    assert exc.value.status_code == 403
    assert {org for _, org in seen["calls"]} == {"langchain-ai", "anthropics"}
