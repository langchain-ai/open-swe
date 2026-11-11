from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException

from openswe.dashboard.deps import session_is_admin
from openswe.dashboard.langsmith_login import LangSmithLogin
from openswe.dashboard.principal import DashboardPrincipal
from openswe.threads.principals import Principal
from openswe.users import User
from openswe.users.models import UserIdentity


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_id", [None, "invalid", "00000000-0000-0000-0000-000000000001"])
async def test_github_session_resolves_current_user(monkeypatch, raw_id):
    user = User()
    resolve = AsyncMock(return_value=user)
    monkeypatch.setattr(User, "for_session", resolve)
    session = {"sub": "Alice"}
    if raw_id is not None:
        session["user_id"] = raw_id

    principal = DashboardPrincipal.from_session(session)

    assert principal.github_login == "Alice"
    assert await principal.user() is user
    resolve.assert_awaited_once_with(
        UUID(raw_id) if raw_id not in (None, "invalid") else None, "Alice"
    )


@pytest.mark.parametrize(
    "session",
    [
        {},
        {"sub": ""},
        {"sub": " "},
        {"sub": 123},
        {"provider": "unknown", "sub": "alice"},
    ],
)
def test_session_identity_rejects_invalid_principals(session):
    with pytest.raises(HTTPException) as exc:
        DashboardPrincipal.from_session(session)
    assert exc.value.status_code == 401
    with pytest.raises(HTTPException) as exc:
        Principal.of_person(session)
    assert exc.value.status_code == 401


def test_a_langsmith_only_session_can_play_trace_flight_but_not_reach_github_features():
    user_id = "00000000-0000-0000-0000-000000000001"
    principal = DashboardPrincipal.from_session(
        {
            "provider": "langsmith",
            "sub": f"user:{user_id}",
            "user_id": user_id,
            "langsmith_subject": "ls-1",
        }
    )
    principal.authorize_path("/dashboard/api/trace-flight/runs", "POST")
    with pytest.raises(HTTPException) as exc:
        principal.authorize_path("/dashboard/api/workspaces", "POST")
    assert exc.value.status_code == 403


def test_github_thread_identity_preserves_attribution(monkeypatch):
    monkeypatch.setenv("CONFIGURED_ADMINS", "alice")
    principal = Principal.of_person({"provider": "github", "sub": " Alice "})

    assert principal.person == "Alice"
    assert principal.sender_id == "github:Alice"
    assert principal.admin
    principal.authorize("system")


@pytest.mark.parametrize(
    ("login", "admin"),
    [("alice", True), ("mallory", False), ("user:00000000-0000-0000-0000-000000000001", False)],
)
def test_langsmith_session_is_admin_only_through_its_linked_github_login(
    monkeypatch, login: str, admin: bool
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "alice,admin@example.com")
    session = {
        "sub": login,
        "provider": "langsmith",
        "user_id": "00000000-0000-0000-0000-000000000001",
        "langsmith_subject": "ls-sub",
        "email": "admin@example.com",
    }

    assert session_is_admin(session) is admin


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["github", "langsmith"])
async def test_every_dashboard_session_requires_langsmith(monkeypatch, provider):
    user = User()
    monkeypatch.setattr(DashboardPrincipal, "user", AsyncMock(return_value=user))
    authorize = AsyncMock(side_effect=HTTPException(401, "Sign in with LangSmith"))
    monkeypatch.setattr(LangSmithLogin, "authorize_user", authorize)
    principal = DashboardPrincipal(provider, "alice", None)
    with pytest.raises(HTTPException) as exc:
        await principal.authorize()
    assert exc.value.status_code == 401
    authorize.assert_awaited_once_with(user)


@pytest.mark.parametrize(
    "route",
    ["slack/login", "slack/callback", "slack/desktop/exchange", "notifications/receipt/delivered"],
)
def test_langsmith_only_person_can_use_personal_routes(route):
    principal = DashboardPrincipal("langsmith", "user:00000000-0000-0000-0000-000000000001", None)
    principal.authorize_path(f"/dashboard/api/{route}", "POST")


def test_langsmith_only_person_can_list_apps_but_not_access_github_app_routes():
    principal = DashboardPrincipal("langsmith", "user:00000000-0000-0000-0000-000000000001", None)
    principal.authorize_path("/dashboard/api/plugins", "GET")
    with pytest.raises(HTTPException):
        principal.authorize_path("/dashboard/api/plugins/install/github/owner/repo", "GET")


@pytest.mark.asyncio
async def test_github_identity_alone_is_not_langsmith_authentication():
    user = User(identities=[UserIdentity(provider="github", external_id="42", login="alice")])
    with pytest.raises(HTTPException) as exc:
        await LangSmithLogin().authorize_user(user)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_linked_langsmith_identity_still_requires_valid_authorization(monkeypatch, status):
    user = User(identities=[UserIdentity(provider="langsmith", external_id="ls-alice")])
    authorize = AsyncMock(side_effect=HTTPException(status, "Invalid LangSmith authorization"))
    monkeypatch.setattr(LangSmithLogin, "authorize_session", authorize)
    with pytest.raises(HTTPException) as exc:
        await LangSmithLogin().authorize_user(user)
    assert exc.value.status_code == status
    authorize.assert_awaited_once_with(
        DashboardPrincipal("langsmith", f"user:{user.id}", user.id, "ls-alice")
    )
