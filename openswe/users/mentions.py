"""People mentioned in text by their Open SWE user id."""

import asyncio
import re
from typing import Self
from uuid import UUID

from pydantic import BaseModel

from openswe.users.models import User

_PERSON_TOKEN = re.compile(r"<@user:([0-9a-fA-F-]{36})\|([^>|]*)>")
_CHANNEL_TOKEN = re.compile(r"<#[CG][A-Z0-9]+\|([^>|]+)>")


class PersonMention(BaseModel):
    """A person the composer mentioned as ``<@user:{users.id}|{name}>``, carrying the name it showed."""

    user_id: UUID
    name: str

    @classmethod
    def all_in(cls, text: str) -> list[Self]:
        """The distinct people ``text`` mentions, in order."""
        seen: dict[UUID, Self] = {}
        for match in _PERSON_TOKEN.finditer(text):
            try:
                mention = cls(user_id=UUID(match[1]), name=match[2])
            except ValueError:
                continue
            seen.setdefault(mention.user_id, mention)
        return list(seen.values())

    @classmethod
    def leads(cls, text: str) -> bool:
        """Whether ``text`` opens by mentioning someone: it is addressed to them, not the agent."""
        match = _PERSON_TOKEN.match(text.lstrip())
        return match is not None and bool(cls.all_in(match[0]))

    @staticmethod
    async def to_slack(text: str) -> str:
        """``text`` with each mention as a Slack ``<@U…>`` mention, or ``@name`` when they have none."""
        mentions = PersonMention.all_in(text)
        if not mentions:
            return text
        people = await asyncio.gather(*(User.get(mention.user_id) for mention in mentions))
        slack_ids = {
            mention.user_id: person.slack_user_id
            for mention, person in zip(mentions, people, strict=True)
            if person is not None and person.slack_user_id
        }

        def replace(match: re.Match[str]) -> str:
            try:
                slack_id = slack_ids.get(UUID(match[1]))
            except ValueError:
                slack_id = None
            return f"<@{slack_id}>" if slack_id else f"@{match[2]}"

        return _PERSON_TOKEN.sub(replace, text)

    @staticmethod
    def readable(text: str) -> str:
        """``text`` with mention tokens shown as ``@name`` and ``#channel``."""
        return _PERSON_TOKEN.sub(r"@\2", _CHANNEL_TOKEN.sub(r"#\1", text))
