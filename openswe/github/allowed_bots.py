"""Admin-allowed GitHub bots whose pull requests are treated like a Robin user's."""

import re
from datetime import datetime
from typing import Self
from uuid import UUID, uuid7

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import ForeignKey, ScalarSelect, delete, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.org_membership import OPEN_SWE_GITHUB_LOGINS
from openswe.users import User
from openswe.users.records import github_login_of

_BOT_LOGIN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,38})\[bot\]")


class AllowGitHubBot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login: str

    def bot_login(self) -> str:
        """The normalized login, or a 400 when it does not name a GitHub App bot."""
        login = self.login.strip().lower()
        if not _BOT_LOGIN.fullmatch(login):
            raise HTTPException(400, "Enter a GitHub App bot login, such as langsmith-engine[bot].")
        return login


class AllowedGitHubBotRow(Base):
    __tablename__ = "allowed_github_bot"

    login: Mapped[str]
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)


class AllowedGitHubBot(BaseModel):
    login: str
    created_by: str = ""
    created_at: datetime

    @classmethod
    async def all(cls) -> list[Self]:
        creator = github_login_of(AllowedGitHubBotRow.created_by)
        async with postgres.session() as session:
            rows = await session.execute(
                select(AllowedGitHubBotRow, creator).order_by(AllowedGitHubBotRow.created_at)
            )
            return [
                cls(login=row.login, created_by=login or "", created_at=row.created_at)
                for row, login in rows.all()
            ]

    @classmethod
    async def get(cls, login: str) -> Self | None:
        return next((bot for bot in await cls.all() if bot.login == login.lower()), None)

    @classmethod
    async def allows(cls, login: str) -> bool:
        """Whether PRs ``login`` opens are treated like a Robin user's."""
        if not login:
            return False
        async with postgres.session() as session:
            return bool(
                await session.scalar(
                    select(exists().where(AllowedGitHubBotRow.login == login.lower()))
                )
            )

    @classmethod
    async def author_allowed(cls, login: str) -> bool:
        """Whether ``login``'s pull requests are treated like a Robin user's."""
        return (
            login.lower() in OPEN_SWE_GITHUB_LOGINS
            or await User.for_login("github", login) is not None
            or await cls.allows(login)
        )

    @classmethod
    async def add(cls, login: str, actor: UUID | ScalarSelect[UUID] | None) -> bool:
        """Allow ``login`` as the person ``actor`` names; ``False`` when it already is."""
        async with postgres.session() as session:
            added = await session.scalar(
                insert(AllowedGitHubBotRow)
                .values(id=uuid7(), login=login, created_by=actor, updated_by=actor)
                .on_conflict_do_nothing(index_elements=[AllowedGitHubBotRow.login])
                .returning(AllowedGitHubBotRow.login)
            )
        return added is not None

    @classmethod
    async def remove(cls, login: str) -> None:
        async with postgres.session() as session:
            await session.execute(
                delete(AllowedGitHubBotRow).where(AllowedGitHubBotRow.login == login.lower())
            )
