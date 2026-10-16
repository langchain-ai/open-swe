"""Admin-authorized Slack bots that can start Open SWE system threads."""

import re
from datetime import UTC, datetime
from typing import Any, Self
from uuid import UUID, uuid7

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import ColumnElement, ForeignKey, ScalarSelect, and_, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.dashboard.oauth import session_user_id
from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.slack.http import (
    SlackClient,
    slack_bot_members,
    slack_cache_key,
    slack_http_errors,
    slack_identity,
)
from openswe.users.records import github_login_of, user_id_for_login
from openswe.utils import ttl_cache


class AllowSlackBot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_id: str

    @field_validator("bot_id")
    @classmethod
    def validate_bot_id(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[BUW][A-Z0-9]{1,31}", value):
            raise ValueError("Enter a Slack bot ID (B…) or bot member ID (U…)")
        return value


class AllowedSlackBotRow(Base):
    __tablename__ = "allowed_slack_bot"

    team_id: Mapped[str]
    bot_id: Mapped[str]
    user_id: Mapped[str]
    app_id: Mapped[str]
    name: Mapped[str]
    image_url: Mapped[str]
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)


# What a bot's record writes; its ``created_by`` is a login, stored as that person's users id.
_RECORD_COLUMNS = ("team_id", "bot_id", "user_id", "app_id", "name", "image_url", "created_at")


class AllowedSlackBot(BaseModel):
    team_id: str
    bot_id: str
    user_id: str = ""
    app_id: str = ""
    name: str
    image_url: str = ""
    created_by: str = ""
    created_at: datetime

    @staticmethod
    def _is(team_id: str, bot_id: str) -> ColumnElement[bool]:
        return and_(AllowedSlackBotRow.team_id == team_id, AllowedSlackBotRow.bot_id == bot_id)

    @classmethod
    async def _select(cls, *where: ColumnElement[bool]) -> list[Self]:
        creator = github_login_of(AllowedSlackBotRow.created_by)
        async with postgres.session() as session:
            rows = await session.execute(
                select(AllowedSlackBotRow, creator)
                .where(*where)
                .order_by(AllowedSlackBotRow.created_at)
            )
            return [
                cls.model_validate(
                    {column: getattr(row, column) for column in _RECORD_COLUMNS}
                    | {"created_by": login or ""}
                )
                for row, login in rows.all()
            ]

    def _record(self) -> dict[str, object]:
        return self.model_dump(include=set(_RECORD_COLUMNS))

    @classmethod
    async def get(cls, team_id: str, bot_id: str) -> Self | None:
        bots = await cls._select(cls._is(team_id, bot_id))
        return bots[0] if bots else None

    @classmethod
    async def all(cls) -> list[Self]:
        return await cls._select()

    async def add(self, actor: UUID | ScalarSelect[UUID] | None) -> bool:
        """Allow the bot as the person ``actor`` names; ``False`` when it already is."""
        async with postgres.session() as session:
            added = await session.scalar(
                insert(AllowedSlackBotRow)
                .values(
                    id=uuid7(),
                    **self._record(),
                    created_by=actor,
                    updated_by=actor,
                )
                .on_conflict_do_nothing(
                    index_elements=[AllowedSlackBotRow.team_id, AllowedSlackBotRow.bot_id]
                )
                .returning(AllowedSlackBotRow.bot_id)
            )
        return added is not None

    @classmethod
    async def remove(cls, team_id: str, bot_id: str) -> None:
        """Revoke the bot."""
        async with postgres.session() as session:
            await session.execute(delete(AllowedSlackBotRow).where(cls._is(team_id, bot_id)))


class SlackBotOption(BaseModel):
    team_id: str
    bot_id: str
    user_id: str
    name: str
    image_url: str = ""


class AllowedSlackBotEntry(BaseModel):
    """What any signed-in user may see of an allowed bot: enough to name its threads."""

    key: str
    name: str
    image_url: str = ""


async def allowed_slack_bot_directory() -> list[AllowedSlackBotEntry]:
    bots = await AllowedSlackBot.all()
    return sorted(
        (
            AllowedSlackBotEntry(
                key=f"{bot.team_id}:{bot.bot_id}", name=bot.name, image_url=bot.image_url
            )
            for bot in bots
        ),
        key=lambda entry: (entry.name.casefold(), entry.key),
    )


async def resolve_allowed_slack_bot(
    team_id: str, bot_id: str, *, user_id: str = "", app_id: str = ""
) -> AllowedSlackBot | None:
    if not team_id or not bot_id:
        return None
    bot = await AllowedSlackBot.get(team_id, bot_id)
    if bot is None:
        return None
    if (user_id and user_id != bot.user_id) or (app_id and app_id != bot.app_id):
        return None
    return bot


async def allow_slack_bot(body: AllowSlackBot, admin: dict[str, Any]) -> AllowedSlackBot:
    login = admin["sub"]
    async with slack_http_errors(), SlackClient.bot() as client:
        auth = await slack_identity(client)
        team_id = auth["team_id"]
        bot_id = body.bot_id
        user: dict[str, Any] | None = None
        if not bot_id.startswith("B"):
            user = (await client.users_info(user=body.bot_id)).get("user")
            profile = user.get("profile") if isinstance(user, dict) else None
            bot_id = profile.get("bot_id") if isinstance(profile, dict) else None
            if not isinstance(bot_id, str) or not bot_id:
                raise HTTPException(400, "That Slack member is not a bot.")
        bot = (await client.bots_info(bot=bot_id)).get("bot")
        if not isinstance(bot, dict) or bot.get("deleted") or bot.get("id") != bot_id:
            raise HTTPException(400, "That Slack bot is unavailable.")
        user_id = bot.get("user_id") or ""
        if bot_id == auth.get("bot_id") or (user_id and user_id == auth.get("user_id")):
            raise HTTPException(400, "Open SWE cannot trigger itself.")
        if user_id and user is None:
            user = (await client.users_info(user=user_id)).get("user")
        if (user_id or user is not None) and (
            not isinstance(user, dict)
            or user.get("is_bot") is not True
            or user.get("deleted")
            or user.get("team_id") != team_id
            or user.get("id") != user_id
        ):
            raise HTTPException(400, "Choose an active bot in this Slack workspace.")
    profile = user.get("profile") if isinstance(user, dict) else None
    image_url = profile.get("image_48", "") if isinstance(profile, dict) else ""
    record = AllowedSlackBot(
        team_id=team_id,
        bot_id=bot_id,
        user_id=user_id,
        app_id=bot.get("app_id") or "",
        name=bot.get("name") or bot_id,
        image_url=image_url
        if isinstance(image_url, str) and image_url.startswith("https://")
        else "",
        created_by=login,
        created_at=datetime.now(UTC),
    )
    if not await record.add(session_user_id(admin) or user_id_for_login(login)):
        raise HTTPException(409, "This bot is already allowed.")
    return await AllowedSlackBot.get(team_id, bot_id) or record


async def list_slack_bots() -> list[SlackBotOption]:
    async with slack_http_errors(), SlackClient.bot() as client:

        async def load() -> list[SlackBotOption]:
            auth = await slack_identity(client)
            team_id = auth["team_id"]
            bots: dict[str, SlackBotOption] = {}
            async for member in slack_bot_members(client):
                if (
                    member.get("is_bot") is not True
                    or member.get("deleted")
                    or member.get("team_id") != team_id
                    or member.get("id") in {auth.get("user_id"), "USLACKBOT"}
                ):
                    continue
                profile = member.get("profile")
                if not isinstance(profile, dict):
                    continue
                bot_id, user_id = profile.get("bot_id"), member.get("id")
                if not isinstance(bot_id, str) or not bot_id or bot_id == auth.get("bot_id"):
                    continue
                if not isinstance(user_id, str) or not user_id:
                    continue
                name = (
                    profile.get("display_name")
                    or profile.get("real_name")
                    or member.get("name")
                    or user_id
                )
                image_url = profile.get("image_48") or ""
                bots[bot_id] = SlackBotOption(
                    team_id=team_id,
                    bot_id=bot_id,
                    user_id=user_id,
                    name=name if isinstance(name, str) else user_id,
                    image_url=image_url
                    if isinstance(image_url, str) and image_url.startswith("https://")
                    else "",
                )
            return sorted(bots.values(), key=lambda bot: (bot.name.casefold(), bot.bot_id))

        # A directory is only a suggestion; every selection is reverified when added.
        return await ttl_cache.cached(f"{slack_cache_key(client)}:bot-directory", 300, load)
