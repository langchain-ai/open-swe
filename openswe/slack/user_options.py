"""Slack people available to mention in dashboard conversations."""

from typing import Self

from pydantic import BaseModel

from openswe.slack.http import SlackClient, slack_bot_members, slack_cache_key, slack_http_errors
from openswe.utils import ttl_cache


class SlackUserOption(BaseModel):
    id: str
    name: str
    display_name: str
    real_name: str

    @classmethod
    async def all(cls) -> list[Self]:
        async with slack_http_errors(), SlackClient.bot() as client:
            return await ttl_cache.cached(
                f"{slack_cache_key(client)}:mention-users", 300, lambda: cls.load(client)
            )

    @classmethod
    async def load(cls, client: SlackClient) -> list[Self]:
        users: list[Self] = []
        async for member in slack_bot_members(client):
            if member.get("deleted") or member.get("is_bot") or member.get("id") == "USLACKBOT":
                continue
            profile = member.get("profile")
            if not isinstance(profile, dict):
                profile = {}
            user_id = member.get("id")
            name = member.get("name")
            if not isinstance(user_id, str) or not isinstance(name, str):
                continue
            display_name = profile.get("display_name")
            real_name = profile.get("real_name")
            users.append(
                cls(
                    id=user_id,
                    name=name,
                    display_name=display_name if isinstance(display_name, str) else name,
                    real_name=real_name if isinstance(real_name, str) else name,
                )
            )
        return sorted(users, key=lambda user: (user.display_name or user.name).casefold())


class SlackUserDirectory(BaseModel):
    users: list[SlackUserOption]
