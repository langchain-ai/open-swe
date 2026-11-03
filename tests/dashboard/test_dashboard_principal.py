from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException

from openswe.dashboard.deps import session_is_admin
from openswe.dashboard.principal import DashboardPrincipal
from openswe.threads.principals import Principal
from openswe.users import User


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
