"""The Slack channel directory: one row per channel, keyed by its id.

Rows are both the lookup cache for channel details and the name-to-id map, so a
channel named in a tool call resolves without paging Slack. ``fresh`` bounds how
long a stored payload is trusted before Slack is asked again.
"""

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Self

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from agent.config import ENV
from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.slack.http import SLACK_REQUEST_ERRORS, SlackClient, slack_error
from agent.slack.payloads import SlackChannelContext, SlackChannelPayload, SlackMessage
from agent.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

SLACK_BOT_TOKEN = ENV.SLACK_BOT_TOKEN.get()
PAYLOAD_TTL = timedelta(seconds=300)
HISTORY_MAX_MESSAGES = 100

_MENTION = re.compile(r"^<#([A-Z0-9]+)(?:\|[^>]*)?>$")
_ID_SHAPE = re.compile(r"^[CGD][A-Z0-9]{8,}$")


class SlackChannel(Base):
    __tablename__ = "slack_channel"

    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(server_default="", default="")
    payload: Mapped[JsonObject] = mapped_column(JSONB, default_factory=dict)
    fetched_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    @property
    def fresh(self) -> bool:
        return self.fetched_at is not None and datetime.now(UTC) - self.fetched_at < PAYLOAD_TTL

    @property
    def details(self) -> SlackChannelPayload:
        """A typed read of the stored Slack channel object."""
        return SlackChannelPayload.of(self.payload)

    @property
    def public(self) -> bool:
        """Whether anybody in the workspace can already read this channel."""
        return self.details.is_public

    @property
    def context(self) -> SlackChannelContext:
        """This channel's identity and description, for prompts and thread metadata."""
        return self.details.to_context(self.id)

    @property
    def can_be_kitchen(self) -> bool:
        """Whether Slack delivers this channel's messages and it is not externally shared."""
        return self.context.allows_operations and self.payload.get("is_member") is True

    @classmethod
    def from_payload(cls, payload: JsonObject) -> Self | None:
        """A row for one Slack ``channel`` object; ``None`` when it carries no id."""
        channel_id = payload.get("id")
        if not isinstance(channel_id, str) or not channel_id:
            return None
        name = payload.get("name")
        return cls(
            id=channel_id, name=name.lower() if isinstance(name, str) else "", payload=payload
        )

    @classmethod
    async def save_all(cls, *payloads: JsonObject) -> None:
        """Upsert what Slack just said about these channels."""
        rows = [row for row in (cls.from_payload(item) for item in payloads) if row is not None]
        if not rows or not postgres.configured():
            return
        upsert = insert(cls).values(
            [{"id": row.id, "name": row.name, "payload": row.payload} for row in rows]
        )
        async with postgres.session() as session:
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[cls.id],
                    set_={
                        "name": upsert.excluded.name,
                        "payload": upsert.excluded.payload,
                        "fetched_at": func.clock_timestamp(),
                    },
                )
            )

    @classmethod
    async def _row(cls, channel_id: str) -> Self | None:
        if not postgres.configured():
            return None
        async with postgres.session() as session:
            return await session.get(cls, channel_id)

    @classmethod
    async def _row_named(cls, name: str) -> Self | None:
        if not postgres.configured():
            return None
        async with postgres.session() as session:
            return await session.scalar(select(cls).where(cls.name == name))

    @classmethod
    async def load(cls, channel_id: str, *, use_cache: bool = True) -> Self | None:
        """The channel, from the directory while fresh and from Slack otherwise."""
        if not SLACK_BOT_TOKEN or not channel_id:
            return None
        if use_cache and (known := await cls._row(channel_id)) is not None and known.fresh:
            return known
        try:
            async with SlackClient.bot() as client:
                data = await client.conversations_info(channel=channel_id)
            payload = data.get("channel")
            if isinstance(payload, dict):
                await cls.save_all(payload)
                return cls.from_payload(payload)
        except SLACK_REQUEST_ERRORS as exc:
            logger.warning("Slack channel lookup failed", extra={"slack_error": slack_error(exc)})
        return None

    @classmethod
    async def fetch(cls, channel_id: str, *, use_cache: bool = True) -> JsonObject | None:
        """The raw Slack channel object, for callers reading fields this model omits."""
        channel = await cls.load(channel_id, use_cache=use_cache)
        return dict(channel.payload) if channel is not None else None

    @classmethod
    async def context_for(cls, channel_id: str, *, use_cache: bool = True) -> SlackChannelContext:
        """One channel's context, empty but for its id when the channel is unreadable."""
        channel = await cls.load(channel_id, use_cache=use_cache)
        return channel.context if channel is not None else SlackChannelContext(id=channel_id)

    @classmethod
    async def _search(cls, name: str) -> Self | None:
        """Page ``conversations.list`` for ``name``, saving every channel it walks past."""
        cursor: str | None = None
        try:
            async with SlackClient.bot() as client:
                while True:
                    data = await client.conversations_list(
                        types="public_channel,private_channel",
                        exclude_archived=True,
                        limit=1000,
                        cursor=cursor,
                    )
                    listed = data.get("channels")
                    page = [item for item in listed if isinstance(item, dict)] if listed else []
                    await cls.save_all(*page)
                    for item in page:
                        if item.get("name") == name and (row := cls.from_payload(item)):
                            return row
                    metadata = data.get("response_metadata")
                    cursor = metadata.get("next_cursor") if isinstance(metadata, dict) else None
                    if not cursor:
                        return None
        except SLACK_REQUEST_ERRORS as exc:
            logger.warning("Slack channel list failed", extra={"slack_error": slack_error(exc)})
            return None

    @classmethod
    async def resolve(cls, reference: str) -> Self | None:
        """The channel for an id, a ``<#C…|name>`` mention, ``#name`` or ``name``."""
        if not SLACK_BOT_TOKEN:
            return None
        value = reference.strip()
        if mention := _MENTION.fullmatch(value):
            value = mention.group(1)
        value = value.lstrip("#")
        if not value:
            return None
        if _ID_SHAPE.fullmatch(value):
            return await cls.load(value)
        name = value.lower()
        if (known := await cls._row_named(name)) is not None:
            # A stale row may predate a rename, so confirm it before trusting the name.
            current = known if known.fresh else await cls.load(known.id, use_cache=False)
            if current is not None and current.details.name.lower() == name:
                return current
        return await cls._search(name)

    async def join(self) -> bool:
        """Join this channel; a private one needs an invite instead."""
        if not SLACK_BOT_TOKEN:
            return False
        try:
            async with SlackClient.bot() as client:
                await client.conversations_join(channel=self.id)
            return True
        except SLACK_REQUEST_ERRORS as exc:
            error = slack_error(exc)
            if error == "already_in_channel":
                return True
            logger.warning(
                "Slack channel join failed",
                extra={"slack_channel": self.id, "slack_error": error},
            )
            return False

    async def messages(self, limit: int = 30) -> list[SlackMessage]:
        """The most recent top-level messages, oldest first.

        Empty when the channel may not be read this way, when the fetch failed,
        and when the channel is simply quiet; a caller that has to tell those
        apart checks ``public`` first. Thread replies are not in channel history,
        so a message that has any carries its ``reply_count`` and ``thread_ts``
        for ``slack_read_thread_messages`` to follow.
        """
        if not SLACK_BOT_TOKEN:
            return []
        if not self.public:
            logger.info(
                "Refused to read history for a non-public Slack channel",
                extra={"slack_channel": self.id},
            )
            return []
        capped = max(1, min(limit, HISTORY_MAX_MESSAGES))
        try:
            async with SlackClient.bot() as client:
                payload = await client.conversations_history(channel=self.id, limit=capped)
        except SLACK_REQUEST_ERRORS as exc:
            logger.warning(
                "Slack channel history fetch failed", extra={"slack_error": slack_error(exc)}
            )
            return []
        batch = payload.get("messages")
        if not isinstance(batch, list):
            return []
        messages = [
            message
            for item in batch
            if (message := SlackMessage.parse(item)) is not None and not message.is_noise
        ]
        return sorted(messages, key=lambda message: message.sort_key)
