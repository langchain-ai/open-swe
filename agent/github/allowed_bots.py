"""Admin-allowed third-party GitHub bots whose PR comments may prompt Open SWE."""

import logging
import re
from datetime import datetime
from typing import Self

import httpx2
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from sqlalchemy import BigInteger, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.github.app import get_github_app_installation_token
from agent.github.org_membership import INTERNAL_BOT_LOGINS
from agent.utils.http import DEFAULT_HTTP_TIMEOUT

logger = logging.getLogger(__name__)

_GITHUB_LOGIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?")


class AllowGitHubBot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login: str

    @field_validator("login")
    @classmethod
    def validate_login(cls, value: str) -> str:
        value = value.strip().removeprefix("@")
        if not _GITHUB_LOGIN_RE.fullmatch(value):
            raise ValueError("Enter a GitHub bot login, like dependabot[bot]")
        return value


class _GitHubUser(BaseModel):
    id: int
    login: str
    type: str
    avatar_url: str = ""


class AllowedGitHubBotView(BaseModel):
    github_id: int
    login: str
    avatar_url: str
    created_by: str
    created_at: datetime | None


class AllowedGitHubBot(Base):
    __tablename__ = "allowed_github_bot"

    github_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    login: Mapped[str]
    created_by: Mapped[str]
    avatar_url: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    def view(self) -> AllowedGitHubBotView:
        return AllowedGitHubBotView(
            github_id=self.github_id,
            login=self.login,
            avatar_url=self.avatar_url,
            created_by=self.created_by,
            created_at=self.created_at,
        )

    @classmethod
    async def list_all(cls) -> list[Self]:
        async with postgres.session() as session:
            return list(await session.scalars(select(cls).order_by(func.lower(cls.login))))

    @classmethod
    async def logins(cls) -> frozenset[str]:
        """Lowercased logins of every allowed bot."""
        if not postgres.configured():
            return frozenset()
        async with postgres.session() as session:
            return frozenset(await session.scalars(select(func.lower(cls.login))))

    @classmethod
    async def allow(cls, body: AllowGitHubBot, *, created_by: str) -> Self:
        """Verify the login is a live GitHub bot account, then allow it."""
        account = await _fetch_github_account(body.login)
        if account.type != "Bot":
            raise HTTPException(400, "That GitHub account is not a bot.")
        if account.login.lower() in {login.lower() for login in INTERNAL_BOT_LOGINS}:
            raise HTTPException(400, "Open SWE cannot trigger itself.")
        async with postgres.session() as session:
            inserted = await session.scalar(
                insert(cls)
                .values(
                    github_id=account.id,
                    login=account.login,
                    avatar_url=account.avatar_url
                    if account.avatar_url.startswith("https://")
                    else "",
                    created_by=created_by,
                )
                .on_conflict_do_nothing()
                .returning(cls),
                execution_options={"populate_existing": True},
            )
        if inserted is None:
            raise HTTPException(409, "This bot is already allowed.")
        return inserted

    @classmethod
    async def remove(cls, github_id: int) -> None:
        async with postgres.session() as session:
            await session.execute(delete(cls).where(cls.github_id == github_id))


async def _fetch_github_account(login: str) -> _GitHubUser:
    token = await get_github_app_installation_token(log_errors=False)
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx2.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
            response = await client.get(f"https://api.github.com/users/{login}", headers=headers)
    except httpx2.HTTPError as exc:
        logger.warning("GitHub bot lookup failed", extra={"github_login": login}, exc_info=True)
        raise HTTPException(503, "Could not reach GitHub. Try again.") from exc
    if response.status_code == 404:  # noqa: PLR2004
        raise HTTPException(400, "No GitHub account has that login.")
    if response.status_code != 200:  # noqa: PLR2004
        logger.warning(
            "GitHub bot lookup returned an error",
            extra={"github_login": login, "status_code": response.status_code},
        )
        raise HTTPException(503, "Could not reach GitHub. Try again.")
    try:
        return _GitHubUser.model_validate_json(response.content)
    except ValidationError as exc:
        logger.warning(
            "GitHub bot lookup returned an unexpected body",
            extra={"github_login": login},
            exc_info=True,
        )
        raise HTTPException(503, "GitHub returned an unexpected response.") from exc
