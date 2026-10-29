"""The Slack channel directory: one row per channel, keyed by its id.

Rows are the lookup cache for channel details, the name-to-id map, so a channel
named in a tool call resolves without paging Slack, and the list the dashboard
offers. ``fresh`` bounds how long a stored payload is trusted before Slack is
asked again; ``listed`` marks the live channels the bot can see, kept current by
``sync_directory`` and by Slack's channel events.
"""

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Self

import aiohttp
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_slack_response import AsyncSlackResponse
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.slack.client import post_slack_top_level_message_with_ts
from openswe.slack.http import (
    SLACK_REQUEST_ERRORS,
    SlackClient,
    SlackRequestError,
    slack_error,
    slack_retry_after,
)
from openswe.slack.payloads import SlackChannelContext, SlackChannelPayload, SlackMessage
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

PAYLOAD_TTL = timedelta(seconds=300)
_NEVER_FETCHED = datetime.fromtimestamp(0, UTC)
HISTORY_MAX_MESSAGES = 100

_CHANNEL_TYPES: Final = "public_channel,private_channel"
_PAGE_SIZE: Final = 1000
# conversations.list allows about 20 pages a minute, so a large workspace's walk
# waits out several rate limits; it runs in the background, where that is fine.
_MAX_RATE_LIMITS: Final = 20
_MAX_RATE_LIMIT_WAIT: Final = timedelta(minutes=2)
# Channel events that change what the directory says about one channel.
# Private channels send the group_* forms.
REFETCH_EVENTS: Final = frozenset(
    {"channel_created", "channel_rename", "channel_unarchive", "group_rename", "group_unarchive"}
)
UNLIST_EVENTS: Final = frozenset(
    {"channel_archive", "channel_deleted", "group_archive", "group_deleted"}
)
DIRECTORY_EVENTS: Final = REFETCH_EVENTS | UNLIST_EVENTS

type _PageFetch = Callable[[str | None], Awaitable[AsyncSlackResponse]]

_MENTION = re.compile(r"^<#([A-Z0-9]+)(?:\|[^>]*)?>$")
_ID_SHAPE = re.compile(r"^[CGD][A-Z0-9]{8,}$")


class ChannelLookupUnavailable(RuntimeError):
    def __init__(self, retry_after: int) -> None:
        super().__init__("Slack channel verification is temporarily unavailable")
        self.retry_after = retry_after

    @classmethod
    def from_error(cls, exc: Exception) -> Self | None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", getattr(response, "status", None))
        transient = isinstance(exc, (aiohttp.ClientError, TimeoutError)) or (
            isinstance(exc, SlackApiError)
            and (
                status == 429
                or (isinstance(status, int) and 500 <= status < 600)
                or slack_error(exc).startswith("rate_limited")
                or slack_error(exc) in {"internal_error", "fatal_error", "request_timeout"}
            )
        )
        if not transient:
            return None
        raw_delay = slack_retry_after(exc) or "60"
        delay = int(raw_delay) if re.fullmatch(r"[0-9]{1,8}", raw_delay) else 60
        return cls(max(1, delay))


def _rate_limit_wait(exc: SlackApiError) -> float | None:
    """Seconds Slack asks a rate-limited call to wait, or ``None`` when it was not one."""
    if not slack_error(exc).startswith("rate_limited"):
        return None
    raw = slack_retry_after(exc) or ""
    delay = float(raw) if re.fullmatch(r"[0-9]{1,6}(\.[0-9]+)?", raw) else 1.0
    return min(delay, _MAX_RATE_LIMIT_WAIT.total_seconds())


class SlackChannel(Base):
    __tablename__ = "slack_channel"

    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(server_default="", default="")
    payload: Mapped[JsonObject] = mapped_column(JSONB, default_factory=dict)
    listed: Mapped[bool] = mapped_column(default=False)
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
            id=channel_id,
            name=name.lower() if isinstance(name, str) else "",
            payload=payload,
            listed=SlackChannelPayload.of(payload).listed,
        )

    @classmethod
    async def save_all(cls, *payloads: JsonObject) -> None:
        """Upsert what Slack just said about these channels."""
        rows = [row for row in (cls.from_payload(item) for item in payloads) if row is not None]
        if not rows or not postgres.configured():
            return
        upsert = insert(cls).values(
            [
                {"id": row.id, "name": row.name, "payload": row.payload, "listed": row.listed}
                for row in rows
            ]
        )
        async with postgres.session() as session:
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[cls.id],
                    set_={
                        "name": upsert.excluded.name,
                        "payload": upsert.excluded.payload,
                        "listed": upsert.excluded.listed,
                        "fetched_at": func.clock_timestamp(),
                    },
                )
            )

    @classmethod
    async def listed_channels(cls) -> list[Self]:
        """The directory: every listed channel, by name."""
        if not postgres.configured():
            return []
        async with postgres.session() as session:
            rows = await session.scalars(select(cls).where(cls.listed).order_by(cls.name, cls.id))
            return list(rows)

    @classmethod
    async def sync_directory(cls) -> int:
        """Walk every channel the bot can see into the directory; how many channels Slack sent.

        Public channels come from ``conversations.list``; the bot's own channels,
        which are all private ones it can see, from ``users.conversations``, which
        is also the only reliable word on membership. Once the walk completes, a
        listed channel it did not pass is gone or out of the bot's sight.
        """
        async with postgres.session() as session:
            started = await session.scalar(select(func.clock_timestamp()))
        seen = 0
        async with SlackClient.bot() as client:
            async for page in cls._pages(
                lambda cursor: client.conversations_list(
                    types=_CHANNEL_TYPES, exclude_archived=True, limit=_PAGE_SIZE, cursor=cursor
                ),
                patient=True,
            ):
                await cls.save_all(*page)
                seen += len(page)
            async for page in cls._pages(
                lambda cursor: client.users_conversations(
                    types=_CHANNEL_TYPES, exclude_archived=True, limit=_PAGE_SIZE, cursor=cursor
                ),
                patient=True,
            ):
                await cls.save_all(*({**item, "is_member": True} for item in page))
                seen += len(page)
        async with postgres.session() as session:
            await session.execute(
                update(cls).where(cls.listed, cls.fetched_at < started).values(listed=False)
            )
        return seen

    @classmethod
    async def observe(cls, event_type: str, channel_id: str) -> None:
        """Bring one channel's row up to date after a Slack channel event about it."""
        if event_type in UNLIST_EVENTS:
            if postgres.configured():
                async with postgres.session() as session:
                    await session.execute(
                        update(cls).where(cls.id == channel_id).values(listed=False)
                    )
        elif event_type in REFETCH_EVENTS:
            await cls.load(channel_id, use_cache=False)

    @staticmethod
    async def _pages(
        fetch: _PageFetch, *, patient: bool = False
    ) -> AsyncIterator[list[JsonObject]]:
        """Each page of channels ``fetch`` returns; ``patient`` waits out rate limits."""
        cursor: str | None = None
        seen_cursors: set[str] = set()
        rate_limits = 0
        while True:
            try:
                data = await fetch(cursor)
            except SlackApiError as exc:
                wait = _rate_limit_wait(exc)
                if not patient or wait is None or rate_limits >= _MAX_RATE_LIMITS:
                    raise
                rate_limits += 1
                logger.info(
                    "Slack rate limited a channel walk; waiting",
                    extra={"retry_after_seconds": wait},
                )
                await asyncio.sleep(wait)
                continue
            listed = data.get("channels")
            yield [item for item in listed if isinstance(item, dict)] if listed else []
            metadata = data.get("response_metadata")
            cursor = metadata.get("next_cursor") if isinstance(metadata, dict) else None
            if not cursor:
                return
            if not isinstance(cursor, str) or cursor in seen_cursors:
                raise ValueError("Slack repeated a pagination cursor")
            seen_cursors.add(cursor)

    @classmethod
    async def ensure(cls, session: AsyncSession, *channel_ids: str) -> None:
        """Give each channel a row for other rows to reference, inside ``session``.

        A new row is a stub that never counts as ``fresh`` and matches no name,
        so its details load from Slack on first use.
        """
        if ids := sorted(set(channel_ids)):
            await session.execute(
                insert(cls)
                .values([{"id": channel_id, "fetched_at": _NEVER_FETCHED} for channel_id in ids])
                .on_conflict_do_nothing(index_elements=[cls.id])
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
    async def cached(cls, channel_id: str) -> Self | None:
        """Fresh directory metadata only; never contact Slack for an audit lookup."""
        if not channel_id:
            return None
        known = await cls._row(channel_id)
        return known if known is not None and known.fresh else None

    @classmethod
    async def load(
        cls, channel_id: str, *, use_cache: bool = True, raise_unavailable: bool = False
    ) -> Self | None:
        """The channel, from the directory while fresh and from Slack otherwise."""
        if not channel_id:
            return None
        if use_cache and (known := await cls.cached(channel_id)) is not None:
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
            if raise_unavailable and (unavailable := ChannelLookupUnavailable.from_error(exc)):
                raise unavailable from None
        return None

    @classmethod
    async def fetch(cls, channel_id: str, *, use_cache: bool = True) -> JsonObject | None:
        """The raw Slack channel object, for callers reading fields this model omits."""
        channel = await cls.load(channel_id, use_cache=use_cache)
        return dict(channel.payload) if channel is not None else None

    @classmethod
    async def context_for(cls, channel_id: str, *, use_cache: bool = True) -> SlackChannelContext:
        """One channel's context, empty but for its id when the channel is unreadable."""
        try:
            channel = await cls.load(channel_id, use_cache=use_cache, raise_unavailable=True)
        except ChannelLookupUnavailable as exc:
            return SlackChannelContext(id=channel_id, lookup_retry_after=exc.retry_after)
        return channel.context if channel is not None else SlackChannelContext(id=channel_id)

    @classmethod
    async def _search(cls, name: str) -> Self | None:
        """Page ``conversations.list`` for ``name``, saving every channel it walks past."""
        try:
            async with SlackClient.bot() as client:
                async for page in cls._pages(
                    lambda cursor: client.conversations_list(
                        types=_CHANNEL_TYPES,
                        exclude_archived=True,
                        limit=_PAGE_SIZE,
                        cursor=cursor,
                    )
                ):
                    await cls.save_all(*page)
                    for item in page:
                        if item.get("name") == name and (row := cls.from_payload(item)):
                            return row
            return None
        except SLACK_REQUEST_ERRORS as exc:
            logger.warning("Slack channel list failed", extra={"slack_error": slack_error(exc)})
            return None

    @classmethod
    async def resolve(cls, reference: str) -> Self | None:
        """The channel for an id, a ``<#C…|name>`` mention, ``#name`` or ``name``."""
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

    async def post(
        self, text: str, *, blocks: list[dict[str, Any]] | None = None, login: str | None = None
    ) -> str:
        """Post a top-level message, joining the channel when the bot is outside it."""
        try:
            return await post_slack_top_level_message_with_ts(
                self.id, text, unfurl_links=False, unfurl_media=False, blocks=blocks, login=login
            )
        except SlackRequestError as exc:
            if exc.code != "not_in_channel" or not await self.join():
                raise
        return await post_slack_top_level_message_with_ts(
            self.id, text, unfurl_links=False, unfurl_media=False, blocks=blocks, login=login
        )

    async def messages(self, limit: int = 30) -> list[SlackMessage]:
        """The most recent top-level messages, oldest first.

        Empty when the channel may not be read this way, when the fetch failed,
        and when the channel is simply quiet; a caller that has to tell those
        apart checks ``public`` first. Thread replies are not in channel history,
        so a message that has any carries its ``reply_count`` and ``thread_ts``
        for ``slack_read_thread_messages`` to follow.
        """
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
