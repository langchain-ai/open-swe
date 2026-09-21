"""People, and the provider identities they sign in with.

One ``users`` row is one person: a synthetic UUIDv7 that stays the same however
they reach Open SWE. Each ``user_identity`` row is one provider account — a
GitHub user or a Slack member — keyed by the provider's immutable
``external_id``, so a renamed GitHub login or a new Slack workspace changes a
column rather than a person's identity. Tables that need to name a person
reference ``users.id`` instead of a provider-specific handle.
"""

import logging
from collections.abc import Collection, Iterable
from datetime import datetime
from typing import Literal, Self
from uuid import UUID, uuid7

from sqlalchemy import ForeignKey, Select, Text, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.input_messages import PersonIdentity, split_person_id
from agent.users.authorization import UnauthorizedUser, is_authorized_github_login

logger = logging.getLogger(__name__)

Provider = Literal["github", "slack"]


class UserIdentity(Base):
    __tablename__ = "user_identity"

    provider: Mapped[Provider] = mapped_column(Text, primary_key=True)
    external_id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), init=False)
    login: Mapped[str] = mapped_column(server_default="", default="")
    email: Mapped[str] = mapped_column(server_default="", default="")
    team_id: Mapped[str] = mapped_column(server_default="", default="")
    linked_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    display_name: Mapped[str] = mapped_column(server_default="", default="")
    avatar_url: Mapped[str] = mapped_column(server_default="", default="")
    is_admin: Mapped[bool] = mapped_column(default=False)
    identities: Mapped[list[UserIdentity]] = relationship(
        default_factory=list,
        cascade="all, delete-orphan",
        order_by=lambda: (UserIdentity.provider, UserIdentity.external_id),
    )
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    def login_for(self, provider: Provider) -> str:
        """This person's handle on ``provider``, for display; never an identity key."""
        return self._identity_field(provider, "login") or self.display_name or str(self.id)[:8]

    @property
    def github_login(self) -> str:
        """The GitHub handle, or ``""`` — unlike :meth:`login_for`, never a stand-in."""
        return self._identity_field("github", "login")

    @property
    def slack_user_id(self) -> str:
        return self._identity_field("slack", "external_id")

    @property
    def email(self) -> str:
        """The address to reach this person, work address first.

        Slack's wins because it is verified against the workspace, where the
        GitHub one may be a personal account that is not an org member — the
        distinction :func:`agent.threads.access.resolve_run_email` turns on.
        """
        return (
            self._identity_field("slack", "email")
            or self._identity_field("github", "email")
            or next((identity.email for identity in self.identities if identity.email), "")
        )

    def _identity_field(
        self, provider: Provider, field: Literal["login", "email", "external_id"]
    ) -> str:
        return next(
            (
                getattr(identity, field)
                for identity in self.identities
                if identity.provider == provider
            ),
            "",
        )

    @classmethod
    async def get(cls, user_id: UUID) -> Self | None:
        async with postgres.session() as session:
            return await cls._load(session, user_id)

    @classmethod
    async def for_email(cls, email: str) -> Self | None:
        """The person one of whose identities carries ``email``; most recently seen wins."""
        if not postgres.configured():
            return None
        async with postgres.session() as session:
            return await session.scalar(
                cls._with_identities(select(cls))
                .join(cls.identities)
                .where(func.lower(UserIdentity.email) == email.strip().lower())
                .order_by(UserIdentity.last_seen_at.desc())
                .limit(1)
            )

    @classmethod
    async def for_person(cls, person: PersonIdentity) -> Self | None:
        """The person an ingress named, or ``None``.

        Every ingress — Slack events and clicks, GitHub webhooks, run dispatch —
        names people as ``PersonIdentity`` records. Resolution never creates one:
        people come into being when they sign in, so an unknown identity resolves
        to ``None`` and the caller decides what that means.
        """
        platform, external_id = split_person_id(person)
        if platform == "slack" and external_id:
            return await cls.for_identity("slack", external_id)
        if platform == "github" and external_id:
            return await cls._for_github_person(external_id, person)
        login = person.get("github_login", "")
        if login:
            return await cls.for_login("github", login)
        email = person.get("email", "")
        return await cls.for_email(email) if email else None

    @classmethod
    async def _for_github_person(cls, external_id: str, person: PersonIdentity) -> Self | None:
        if external_id.isdigit():
            user = await cls.for_identity("github", external_id)
            if user is not None:
                return user
        login = person.get("github_login") or (external_id if not external_id.isdigit() else "")
        return await cls.for_login("github", login) if login else None

    @classmethod
    async def canonical_person(cls, person: PersonIdentity) -> PersonIdentity:
        """``person`` re-keyed on the ``users`` row behind it, unchanged when unknown.

        One person reaching Open SWE from Slack and from the dashboard is one
        entity the model can match across surfaces, instead of two whose
        relationship it has to infer. Provider handles stay on the record.
        """
        user = await cls.for_person(person)
        return person if user is None else user.as_person(person)

    def as_person(self, person: PersonIdentity) -> PersonIdentity:
        """``person`` keyed on this row, keeping what the surface named them.

        Only the identity key changes. The display fields are what the surface
        observed and what a transcript renders, so filling them from this row
        would rewrite how existing messages read.
        """
        canonical: PersonIdentity = {**person, "id": f"user:{self.id}"}
        platform, _external_id = split_person_id(person)
        if platform:
            canonical["platform"] = platform
        if not canonical.get("github_login") and self.github_login:
            canonical["github_login"] = self.github_login
        return canonical

    @classmethod
    async def login_for_slack(cls, slack_user_id: str | None) -> str | None:
        """GitHub login of the person behind a Slack member id, if they are known."""
        if not slack_user_id or not slack_user_id.strip():
            return None
        user = await cls.for_identity("slack", slack_user_id.strip())
        return (user.github_login or None) if user is not None else None

    @classmethod
    async def login_for_email(cls, email: str | None) -> str | None:
        """GitHub login of the person reachable at ``email``, if they are known."""
        if not email or not email.strip():
            return None
        user = await cls.for_email(email)
        return (user.github_login or None) if user is not None else None

    @classmethod
    async def email_for_login(cls, login: str | None) -> str | None:
        if not login or not login.strip():
            return None
        user = await cls.for_login("github", login.strip())
        return (user.email or None) if user is not None else None

    @classmethod
    async def known_logins(cls, logins: Iterable[str]) -> frozenset[str]:
        """Lowercased GitHub logins among ``logins`` that belong to a person."""
        wanted = {login.strip().lower() for login in logins if login and login.strip()}
        if not wanted or not postgres.configured():
            return frozenset()
        async with postgres.session() as session:
            rows = await session.scalars(
                select(func.lower(UserIdentity.login)).where(
                    UserIdentity.provider == "github",
                    func.lower(UserIdentity.login).in_(wanted),
                )
            )
            return frozenset(rows.all())

    @classmethod
    async def page(cls, *, offset: int, limit: int) -> tuple[list[Self], int]:
        """One page of people, oldest first, with the total count."""
        async with postgres.session() as session:
            total = await session.scalar(select(func.count()).select_from(cls)) or 0
            rows = await session.scalars(
                cls._with_identities(select(cls))
                .order_by(cls.created_at, cls.id)
                .offset(offset)
                .limit(limit)
            )
            return list(rows.all()), total

    @classmethod
    async def for_identity(cls, provider: Provider, external_id: str) -> Self | None:
        """The person owning that provider account, or ``None``."""
        if not postgres.configured():
            return None
        async with postgres.session() as session:
            return await session.scalar(
                cls._with_identities(select(cls))
                .join(cls.identities)
                .where(
                    UserIdentity.provider == provider,
                    UserIdentity.external_id == external_id,
                )
            )

    @classmethod
    async def for_login(cls, provider: Provider, login: str) -> Self | None:
        """The person behind a mutable handle; the most recently seen one wins."""
        if not postgres.configured():
            return None
        async with postgres.session() as session:
            return await session.scalar(
                cls._with_identities(select(cls))
                .join(cls.identities)
                .where(
                    UserIdentity.provider == provider,
                    func.lower(UserIdentity.login) == login.lower(),
                )
                .order_by(UserIdentity.last_seen_at.desc())
                .limit(1)
            )

    @classmethod
    async def sign_in(
        cls,
        provider: Provider,
        external_id: str,
        *,
        login: str = "",
        email: str = "",
        team_id: str = "",
        display_name: str = "",
        avatar_url: str = "",
        admin: bool | None = None,
    ) -> Self:
        """Get or create the person behind a provider account, in one transaction.

        Known values win; empty ones leave what is stored alone. ``admin`` is
        written only when given, so callers without an opinion leave it be.
        Creating a person requires an authorized GitHub login and raises
        :class:`UnauthorizedUser` otherwise; an existing one is only updated.
        """
        async with postgres.session() as session:
            user_id = await cls._claim(
                session, provider, external_id, login=login, email=email, team_id=team_id
            )
            await session.execute(
                update(cls)
                .where(cls.id == user_id)
                .values(
                    last_seen_at=func.clock_timestamp(),
                    **_known(display_name=display_name, avatar_url=avatar_url),
                    **({} if admin is None else {"is_admin": admin}),
                )
            )
            await session.flush()
            stored = await cls._load(session, user_id)
        if stored is None:
            raise RuntimeError(f"user {user_id} vanished during sign in")
        return stored

    async def link(
        self,
        provider: Provider,
        external_id: str,
        *,
        login: str = "",
        email: str = "",
        team_id: str = "",
    ) -> Self:
        """Attach another provider account to this person, taking it over if needed."""
        cls = type(self)
        async with postgres.session() as session:
            upsert = insert(UserIdentity).values(
                user_id=self.id,
                provider=provider,
                external_id=external_id,
                login=login,
                email=email,
                team_id=team_id,
            )
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[UserIdentity.provider, UserIdentity.external_id],
                    set_={
                        "user_id": upsert.excluded.user_id,
                        "last_seen_at": func.clock_timestamp(),
                        **_known(login=login, email=email, team_id=team_id),
                    },
                )
            )
            await session.flush()
            stored = await cls._load(session, self.id)
        if stored is None:
            raise RuntimeError(f"user {self.id} vanished during link")
        return stored

    @classmethod
    async def sync_admins(cls, admins: Collection[str]) -> int:
        """Make ``is_admin`` match ``admins``: GitHub logins or identity emails.

        Returns how many rows changed.
        """
        wanted = [entry.strip().lower() for entry in admins if entry.strip()]
        listed = select(UserIdentity.user_id).where(
            or_(
                (UserIdentity.provider == "github") & func.lower(UserIdentity.login).in_(wanted),
                func.lower(UserIdentity.email).in_(wanted),
            )
        )
        async with postgres.session() as session:
            changed = await session.scalars(
                update(cls)
                .where(cls.is_admin != cls.id.in_(listed))
                .values(is_admin=cls.id.in_(listed))
                .returning(cls.id)
            )
            count = len(changed.all())
        logger.info("Synced admins from configuration", extra={"changed_users": count})
        return count

    @classmethod
    async def _claim(
        cls,
        session: AsyncSession,
        provider: Provider,
        external_id: str,
        *,
        login: str,
        email: str,
        team_id: str,
    ) -> UUID:
        """The id of the person owning this identity, creating them when new.

        The identity's unique key is the only serialization point, so a new
        person is inserted speculatively first — the foreign key needs the row —
        and rolled back when the identity upsert reports that a concurrent first
        sign in already claimed it.
        """
        owner = await session.scalar(
            select(UserIdentity.user_id).where(
                UserIdentity.provider == provider,
                UserIdentity.external_id == external_id,
            )
        )
        speculative = uuid7() if owner is None else None
        if speculative is not None:
            await _authorize(provider, login)
            await session.execute(insert(cls).values(id=speculative))
        upsert = insert(UserIdentity).values(
            user_id=owner or speculative,
            provider=provider,
            external_id=external_id,
            login=login,
            email=email,
            team_id=team_id,
        )
        claimed = await session.scalar(
            upsert.on_conflict_do_update(
                index_elements=[UserIdentity.provider, UserIdentity.external_id],
                set_={
                    "last_seen_at": func.clock_timestamp(),
                    **_known(login=login, email=email, team_id=team_id),
                },
            ).returning(UserIdentity.user_id)
        )
        if claimed is None:
            raise RuntimeError(f"identity {provider}:{external_id} vanished during sign in")
        if speculative is not None and claimed != speculative:
            await session.execute(delete(cls).where(cls.id == speculative))
            logger.info(
                "Concurrent first sign in resolved to an existing user",
                extra={"user_provider": provider, "user_id": str(claimed)},
            )
        return claimed

    @classmethod
    async def _load(cls, session: AsyncSession, user_id: UUID) -> Self | None:
        return await session.scalar(
            cls._with_identities(select(cls))
            .where(cls.id == user_id)
            .execution_options(populate_existing=True)
        )

    @classmethod
    def _with_identities(cls, statement: Select[tuple[Self]]) -> Select[tuple[Self]]:
        return statement.options(selectinload(cls.identities))


def _known(**values: str) -> dict[str, str]:
    """Only the values a caller actually knows, so empty ones never clobber."""
    return {name: value for name, value in values.items() if value}


async def _authorize(provider: Provider, login: str) -> None:
    """Refuse to create a person who may not use Open SWE.

    GitHub membership is the only proof of authorization, so a Slack account
    reaches a user record by ``link``-ing to one, never by creating its own.
    """
    if provider != "github":
        raise UnauthorizedUser(f"a {provider} account cannot establish a new user")
    if not await is_authorized_github_login(login):
        logger.warning(
            "Refused to create a user for an unauthorized GitHub login",
            extra={"github_login": login},
        )
        raise UnauthorizedUser(f"{login or '(no login)'} is not authorized to use Open SWE")
