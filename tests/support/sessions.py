"""Dashboard sessions, and the people behind them, for tests without a users table."""

from collections.abc import Callable, Iterable
from uuid import UUID, uuid5

import pytest

from openswe.dashboard import oauth
from openswe.dashboard.oauth import DashboardSession
from openswe.database import postgres
from openswe.users.models import Provider, User, UserIdentity

_NAMESPACE = UUID("6f1c6b5e-3a52-4c3e-9a51-7f0b2d1e4c11")


def user_id_of(login: str) -> UUID:
    """The id ``signed_in`` and ``SessionUsers`` give ``login`` unless told another."""
    return uuid5(_NAMESPACE, login.lower())


def owned_by(login: str) -> dict[str, str]:
    """Thread metadata naming ``login``'s person, under the id ``signed_in`` gives them."""
    return {"owner_login": login, "owner_user_id": str(user_id_of(login))}


def signed_in(
    login: str, email: str | None = None, *, user_id: UUID | None = None
) -> DashboardSession:
    """``login``'s session, under the same user id for every call with that login."""
    return DashboardSession(user_id=user_id or user_id_of(login), login=login, email=email)


class SessionUsers:
    """People the ``User`` lookups find first, before any database."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._users: dict[UUID, User] = {}
        get, for_login, for_identity = User.get, User.for_login, User.for_identity
        for_identities, for_email = User.for_identities, User.for_email
        stored = postgres.configured

        async def fake_get(user_id: UUID) -> User | None:
            if (user := self._users.get(user_id)) is not None or not stored():
                return user
            return await get(user_id)

        async def fake_for_login(provider: Provider, login: str) -> User | None:
            user = self._find(provider, lambda i: bool(login) and i.login.lower() == login.lower())
            if user is not None or not stored():
                return user
            return await for_login(provider, login)

        async def fake_for_identity(provider: Provider, external_id: str) -> User | None:
            user = self._find(provider, lambda i: i.external_id == external_id)
            if user is not None or not stored():
                return user
            return await for_identity(provider, external_id)

        async def fake_for_identities(
            provider: Provider, external_ids: Iterable[str]
        ) -> dict[str, User]:
            wanted = list(external_ids)
            found = {
                external_id: user
                for external_id in wanted
                if (user := self._find(provider, lambda i, e=external_id: i.external_id == e))
            }
            if found or not stored():
                return found
            return await for_identities(provider, wanted)

        async def fake_for_email(email: str) -> User | None:
            user = next(
                (u for u in self._users.values() if u.email.lower() == email.strip().lower()),
                None,
            )
            if user is not None or not stored():
                return user
            return await for_email(email)

        monkeypatch.setattr(User, "get", fake_get)
        monkeypatch.setattr(User, "for_login", fake_for_login)
        monkeypatch.setattr(User, "for_identity", fake_for_identity)
        monkeypatch.setattr(User, "for_identities", fake_for_identities)
        monkeypatch.setattr(User, "for_email", fake_for_email)

    def _find(self, provider: Provider, matches: Callable[[UserIdentity], bool]) -> User | None:
        return next(
            (
                user
                for user in self._users.values()
                for identity in user.identities
                if identity.provider == provider and matches(identity)
            ),
            None,
        )

    def add(
        self, login: str, *, user_id: UUID | None = None, email: str = "", slack_id: str = ""
    ) -> User:
        """``login``'s person, reachable at ``email`` and from ``slack_id`` when given."""
        identities = [UserIdentity(provider="github", external_id=login, login=login, email=email)]
        if slack_id:
            identities.append(UserIdentity(provider="slack", external_id=slack_id, email=email))
        user = User(id=user_id or user_id_of(login), identities=identities)
        self._users[user.id] = user
        return user

    def cookie(self, login: str, *, user_id: UUID | None = None, email: str | None = None) -> str:
        """A session token for ``login``, who is added if they are not yet known."""
        user = self.add(login, user_id=user_id)
        return oauth.issue_session(login=login, email=email, avatar_url=None, user_id=str(user.id))


@pytest.fixture
def session_users(monkeypatch: pytest.MonkeyPatch) -> SessionUsers:
    return SessionUsers(monkeypatch)
