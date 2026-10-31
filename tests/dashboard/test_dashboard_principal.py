from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException

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

