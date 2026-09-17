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
from agent.slack.http import SLACK_REQUEST_ERRORS, slack_client, slack_error
from agent.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

SLACK_BOT_TOKEN = ENV.SLACK_BOT_TOKEN.get()
PAYLOAD_TTL = timedelta(seconds=300)

SlackChannelContext = dict[str, str | bool | None]

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
    async def fetch(cls, channel_id: str, *, use_cache: bool = True) -> JsonObject | None:
        """Slack channel details (including topic and purpose) for one channel id."""
        if not SLACK_BOT_TOKEN or not channel_id:
            return None
        if use_cache and (known := await cls._row(channel_id)) is not None and known.fresh:
            return dict(known.payload)
        try:
            async with slack_client(token=SLACK_BOT_TOKEN) as client:
                data = await client.conversations_info(channel=channel_id)
            payload = data.get("channel")
            if isinstance(payload, dict):
                await cls.save_all(payload)
                return dict(payload)
        except SLACK_REQUEST_ERRORS as exc:
            logger.warning("Slack channel lookup failed", extra={"slack_error": slack_error(exc)})
        return None

    @classmethod
    async def _search(cls, name: str) -> str | None:
        """Page ``conversations.list`` for ``name``, saving every channel it walks past."""
        cursor: str | None = None
        try:
            async with slack_client(token=SLACK_BOT_TOKEN) as client:
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
                        channel_id = item.get("id")
                        if item.get("name") == name and isinstance(channel_id, str) and channel_id:
                            return channel_id
                    metadata = data.get("response_metadata")
                    cursor = metadata.get("next_cursor") if isinstance(metadata, dict) else None
                    if not cursor:
                        return None
        except SLACK_REQUEST_ERRORS as exc:
            logger.warning("Slack channel list failed", extra={"slack_error": slack_error(exc)})
            return None

    @classmethod
    async def resolve_id(cls, reference: str) -> str | None:
        """The channel id for an id, a ``<#C…|name>`` mention, ``#name`` or ``name``."""
        if not SLACK_BOT_TOKEN:
            return None
        value = reference.strip()
        if mention := _MENTION.fullmatch(value):
            value = mention.group(1)
        value = value.lstrip("#")
        if not value:
            return None
        if _ID_SHAPE.fullmatch(value):
            payload = await cls.fetch(value)
            found = payload.get("id") if payload else None
            return found if isinstance(found, str) and found else None
        name = value.lower()
        if (known := await cls._row_named(name)) is not None:
            # A stale row may predate a rename, so confirm it before trusting the name.
            current = known.payload if known.fresh else await cls.fetch(known.id)
            if current is not None and current.get("name") == name:
                return known.id
        return await cls._search(name)

    @classmethod
    async def is_public(cls, channel_id: str) -> bool:
        """Whether a channel is one anybody in the workspace can already read.

        Channel history is fetched with the deployment's bot token, which says
        nothing about who is asking, so only a channel with no membership to leak
        may be read this way: not private, not a DM or group DM, and not shared with
        another organization.
        """
        payload = await cls.fetch(channel_id)
        if not isinstance(payload, dict):
            return False
        return (
            payload.get("is_channel") is True
            and payload.get("is_private") is False
            and payload.get("is_im") is not True
            and payload.get("is_mpim") is not True
            and payload.get("is_ext_shared") is False
            and payload.get("is_pending_ext_shared") is False
        )

    @classmethod
    def section(cls, payload: JsonObject | None, key: str) -> str:
        if not isinstance(payload, dict):
            return ""
        section = payload.get(key)
        if isinstance(section, dict):
            value = section.get("value")
            if isinstance(value, str):
                return value.strip()
        value = payload.get(key)
        return value.strip() if isinstance(value, str) else ""

    @classmethod
    def topic_and_purpose(cls, payload: JsonObject | None) -> str:
        """A channel's topic and purpose text joined into one string."""
        parts = [value for key in ("topic", "purpose") if (value := cls.section(payload, key))]
        return "\n".join(parts)

    @classmethod
    def normalize_context(cls, channel_id: str, payload: JsonObject | None) -> SlackChannelContext:
        """Normalize Slack channel details for prompts and metadata."""
        name = ""
        name_normalized = ""
        if isinstance(payload, dict):
            raw_name = payload.get("name")
            raw_normalized = payload.get("name_normalized")
            if isinstance(raw_name, str):
                name = raw_name.strip()
            if isinstance(raw_normalized, str):
                name_normalized = raw_normalized.strip()
        topic = cls.section(payload, "topic")
        purpose = cls.section(payload, "purpose")
        description = "\n".join(value for value in (topic, purpose) if value)
        is_ext_shared = payload.get("is_ext_shared") if isinstance(payload, dict) else None
        is_pending_ext_shared = (
            payload.get("is_pending_ext_shared") if isinstance(payload, dict) else None
        )
        is_im = payload.get("is_im") if isinstance(payload, dict) else None
        return {
            "id": channel_id,
            "name": name,
            "name_normalized": name_normalized,
            "topic": topic,
            "purpose": purpose,
            "description": description,
            "is_ext_shared": is_ext_shared if isinstance(is_ext_shared, bool) else None,
            "is_pending_ext_shared": (
                is_pending_ext_shared if isinstance(is_pending_ext_shared, bool) else None
            ),
            "is_im": is_im if isinstance(is_im, bool) else None,
        }

    @classmethod
    async def context(cls, channel_id: str, *, use_cache: bool = True) -> SlackChannelContext:
        """Normalized context for one channel id."""
        return cls.normalize_context(channel_id, await cls.fetch(channel_id, use_cache=use_cache))

    @classmethod
    async def description(cls, channel_id: str) -> str:
        """One channel's combined topic and purpose text."""
        return cls.topic_and_purpose(await cls.fetch(channel_id))

    @classmethod
    def allows_operations(cls, context: SlackChannelContext | None) -> bool:
        """Allow operations only where Slack confirms the channel is not externally shared."""
        if not isinstance(context, dict):
            return False
        return context.get("is_im") is True or (
            context.get("is_ext_shared") is False and context.get("is_pending_ext_shared") is False
        )

    @classmethod
    def context_description(cls, context: SlackChannelContext | None) -> str:
        """Prompt-safe description text from normalized context."""
        if not isinstance(context, dict):
            return ""
        description = context.get("description")
        if isinstance(description, str) and description.strip():
            return description.strip()
        parts: list[str] = []
        for key in ("topic", "purpose"):
            value = context.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        return "\n".join(parts)

    @classmethod
    def context_has_metadata(cls, context: SlackChannelContext | None) -> bool:
        """Whether normalized context carries any name or description field."""
        if not isinstance(context, dict):
            return False
        return any(
            isinstance(value, str) and value.strip()
            for key in ("name", "name_normalized", "topic", "purpose", "description")
            if (value := context.get(key)) is not None
        )

    @classmethod
    def context_is_named(cls, context: SlackChannelContext | None, expected: str) -> bool:
        """Whether normalized context matches a Slack channel name."""
        if not isinstance(context, dict):
            return False
        wanted = expected.strip().lower()
        return any(
            isinstance(value, str) and value.strip().lower() == wanted
            for value in (context.get("name"), context.get("name_normalized"))
        )
