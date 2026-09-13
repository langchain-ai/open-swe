"""Admin-authorized Slack bots that can start Open SWE system threads."""

import re
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, field_validator

from agent.slack.http import (
    slack_bot_members,
    slack_cache_key,
    slack_client,
    slack_http_errors,
    slack_identity,
)
from agent.store import TypedStore, now_iso
from agent.utils import ttl_cache


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


class AllowedSlackBot(BaseModel):
    team_id: str
    bot_id: str
    user_id: str = ""
    app_id: str = ""
    name: str
    image_url: str = ""
    created_by: str = ""
    created_at: str


class SlackBotOption(BaseModel):
    team_id: str
    bot_id: str
    user_id: str
    name: str
    image_url: str = ""


ALLOWED_SLACK_BOTS = TypedStore(["allowed_slack_bots"], AllowedSlackBot)


async def resolve_allowed_slack_bot(
    team_id: str, bot_id: str, *, user_id: str = "", app_id: str = ""
) -> AllowedSlackBot | None:
    if not team_id or not bot_id:
        return None
    bot = await ALLOWED_SLACK_BOTS.get(f"{team_id}:{bot_id}")
    if bot is None or bot.team_id != team_id or bot.bot_id != bot_id:
        return None
    if (user_id and user_id != bot.user_id) or (app_id and app_id != bot.app_id):
        return None
    return bot


async def allow_slack_bot(body: AllowSlackBot, admin: dict[str, Any]) -> AllowedSlackBot:
    login = admin["sub"]
    async with slack_http_errors(), slack_client() as client:
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
    key = f"{team_id}:{bot_id}"
    if await ALLOWED_SLACK_BOTS.get(key) is not None:
        raise HTTPException(409, "This bot is already allowed.")
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
        created_at=now_iso(),
    )
    return await ALLOWED_SLACK_BOTS.put(key, record)


async def list_slack_bots() -> list[SlackBotOption]:
    async with slack_http_errors(), slack_client() as client:

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
