"""Dashboard sessions for tests that call routes without signing in."""

from uuid import UUID, uuid5

import pytest

from openswe.dashboard import oauth
from openswe.dashboard.oauth import DashboardSession
from openswe.database import postgres
from openswe.users.models import User, UserIdentity

_NAMESPACE = UUID("6f1c6b5e-3a52-4c3e-9a51-7f0b2d1e4c11")


def user_id_of(login: str) -> UUID:
    """The id ``signed_in`` and ``SessionUsers`` give ``login`` unless told another."""
    return uuid5(_NAMESPACE, login.lower())


def signed_in(
    login: str, email: str | None = None, *, user_id: UUID | None = None
) -> DashboardSession:
    """``login``'s session, under the same user id for every call with that login."""
    return DashboardSession(user_id=user_id or user_id_of(login), login=login, email=email)


class SessionUsers:
    """People whose session cookies resolve without rows in the database."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._users: dict[UUID, User] = {}
        stored = User.get

        async def get(user_id: UUID) -> User | None:
            if user_id in self._users:
                return self._users[user_id]
            return await stored(user_id) if postgres.configured() else None

        monkeypatch.setattr(User, "get", get)

    def add(self, login: str, *, user_id: UUID | None = None) -> User:
        user = User(
            id=user_id or user_id_of(login),
            identities=[UserIdentity(provider="github", external_id=login, login=login)],
        )
        self._users[user.id] = user
        return user

    def cookie(self, login: str, *, user_id: UUID | None = None, email: str | None = None) -> str:
        """A session token for ``login``, who is added if they are not yet known."""
        user = self.add(login, user_id=user_id)
        return oauth.issue_session(login=login, email=email, avatar_url=None, user_id=str(user.id))


@pytest.fixture
def session_users(monkeypatch: pytest.MonkeyPatch) -> SessionUsers:
    return SessionUsers(monkeypatch)
