"""What counts as human writing, and how each kind becomes a ``human_text`` row.

Two places see it. The Slack Events API delivers every message a person posts
where Open SWE is a member. The transcript holds every message a person sends an
Open SWE thread from anywhere else: the dashboard, the CLI, Claude Code, GitHub
and Linear. A Slack message that reaches a thread is recorded from Slack alone,
so each message is one row however many places carried it.

Only public writing is kept: messages in public Slack channels and in public
threads. Private channels, DMs and private threads never reach the table, so
nothing searching it can leak them.

A row is written without its embedding, which ``embeddings`` fills in later, so
recording a message never waits on a model call.
"""

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Self

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from openswe.database import postgres
from openswe.input_messages import input_envelopes
from openswe.slack.channels import SlackChannel
from openswe.slack.payloads import SlackEvent, SlackMessage
from openswe.threads.summary import CLAUDE_CODE_SOURCE, thread_is_readable
from openswe.transcript.events import (
    MessageCompleted,
    MessageSender,
    TranscriptEvent,
    TurnRequested,
)

logger = logging.getLogger(__name__)

# Subtypes of a message a person typed; the rest are joins, topic changes and bots.
_AUTHORED_SUBTYPES: Final = frozenset({"", "file_share", "thread_broadcast", "me_message"})
# Thread sources whose prompts arrive as the person's bare text, with no envelope.
_UNWRAPPED_SOURCES: Final = frozenset({CLAUDE_CODE_SOURCE})


def public_thread(metadata: Mapping[str, object]) -> bool:
    """Whether everyone may read the thread: marked public, not merely unmarked."""
    return metadata.get("visibility") == "public" and thread_is_readable(metadata)


_AUTHOR = """
    CASE
        WHEN CAST(:author_id AS uuid) IS NOT NULL
            THEN (SELECT id FROM users WHERE id = CAST(:author_id AS uuid))
        ELSE (
            SELECT user_id FROM user_identity
            WHERE provider = :author_provider
                AND (external_id = :author_key OR lower(login) = lower(:author_key))
            ORDER BY last_seen_at DESC NULLS LAST
            LIMIT 1
        )
    END
"""

_SAVE_SLACK = text(
    f"""
    INSERT INTO human_text (
        id, slack_channel_id, slack_ts, slack_thread_ts, slack_user_id, body, sent_at,
        created_by, updated_by
    )
    SELECT :id, :channel_id, :ts, :thread_ts, :user_id, :body, :sent_at, author, author
    FROM (SELECT {_AUTHOR}) AS resolved (author)
    ON CONFLICT (slack_channel_id, slack_ts) WHERE slack_channel_id IS NOT NULL DO UPDATE SET
        body = EXCLUDED.body,
        embedding = NULL,
        updated_at = clock_timestamp(),
        updated_by = EXCLUDED.updated_by
    WHERE human_text.body IS DISTINCT FROM EXCLUDED.body
    """
)

_FORGET_SLACK = text(
    "DELETE FROM human_text WHERE slack_channel_id = :channel_id AND slack_ts = :ts"
)

_SAVE_THREAD = text(
    f"""
    INSERT INTO human_text (
        id, thread_id, message_id, body, sent_at, created_by, updated_by
    )
    SELECT :id, :thread_id, :message_id, :body, :sent_at, author, author
    FROM (SELECT {_AUTHOR}) AS resolved (author)
    ON CONFLICT (thread_id, message_id) WHERE thread_id IS NOT NULL DO NOTHING
    """
)


@dataclass(frozen=True, kw_only=True)
class Author:
    """Who wrote a message, as the surface that delivered it names them.

    Resolved to an Open SWE user in SQL, so recording a message needs no lookup
    of its own: ``user_id`` when the surface already knew the user, otherwise a
    GitHub or Slack account by id or handle.
    """

    user_id: uuid.UUID | None = None
    provider: str = ""
    key: str = ""

    @classmethod
    def from_sender_id(cls, sender_id: str) -> Self:
        """An envelope's ``sender``: ``user:<uuid>``, ``github:<login>``, ``slack:<id>``..."""
        platform, _, key = sender_id.partition(":")
        if platform == "user":
            try:
                return cls(user_id=uuid.UUID(key))
            except ValueError:
                logger.warning("Unparseable user sender id", extra={"sender_id": sender_id})
                return cls()
        return cls(provider=platform, key=key)

    def params(self) -> dict[str, object]:
        return {"author_id": self.user_id, "author_provider": self.provider, "author_key": self.key}


@dataclass(frozen=True, kw_only=True)
class SlackHumanText:
    """A message a person posted in Slack."""

    channel_id: str
    ts: str
    thread_ts: str
    user_id: str
    body: str

    @classmethod
    def from_event(cls, event: SlackEvent) -> Self | None:
        """The message a person posted or edited, or ``None`` for anything else."""
        if event.type not in {"message", "app_mention"}:
            return None
        if event.subtype == "message_changed":
            message = event.message
        else:
            message = SlackMessage(
                ts=event.ts,
                thread_ts=event.thread_ts,
                user=event.resolve_user_id(),
                text=event.text,
                subtype=event.subtype,
                bot_id=event.bot_id,
                app_id=event.app_id,
            )
        if message is None or message.is_from_bot or message.subtype not in _AUTHORED_SUBTYPES:
            return None
        channel_id = event.resolve_channel_id()
        body = (message.text or "").strip()
        if not (channel_id and message.ts and message.user and body):
            return None
        return cls(
            channel_id=channel_id,
            ts=message.ts,
            thread_ts=message.thread_ts if message.thread_ts != message.ts else "",
            user_id=message.user,
            body=body,
        )

    @property
    def sent_at(self) -> datetime:
        return datetime.fromtimestamp(float(self.ts), UTC)

    async def save(self) -> bool:
        """Record this message, or its new text when it was edited; whether its channel is public.

        Reads the channel as last fetched from Slack, which the webhook refreshes
        before every delivery it handles.
        """
        async with postgres.session() as session:
            channel = await session.get(SlackChannel, self.channel_id)
            if channel is None or not channel.public:
                return False
            await session.execute(
                _SAVE_SLACK,
                {
                    "id": uuid.uuid7(),
                    "channel_id": self.channel_id,
                    "ts": self.ts,
                    "thread_ts": self.thread_ts or None,
                    "user_id": self.user_id,
                    "body": self.body,
                    "sent_at": self.sent_at,
                    **Author(provider="slack", key=self.user_id).params(),
                },
            )
        return True


@dataclass(frozen=True, kw_only=True)
class DeletedSlackMessage:
    channel_id: str
    ts: str

    @classmethod
    def from_event(cls, event: SlackEvent) -> Self | None:
        """The message a ``message_deleted`` event removed."""
        if event.type != "message" or event.subtype != "message_deleted":
            return None
        ts = event.deleted_ts or (event.previous_message.ts if event.previous_message else "")
        channel_id = event.resolve_channel_id()
        return cls(channel_id=channel_id, ts=ts) if channel_id and ts else None

    async def forget(self) -> None:
        async with postgres.transaction() as conn:
            await conn.execute(_FORGET_SLACK, {"channel_id": self.channel_id, "ts": self.ts})


async def observe_slack_event(event: SlackEvent) -> None:
    """Record, revise or forget the message a Slack event describes.

    Runs on the webhook's own path, so a failure is logged rather than raised:
    search missing a message must never cost the delivery.
    """
    if not postgres.configured():
        return
    try:
        if (deleted := DeletedSlackMessage.from_event(event)) is not None:
            await deleted.forget()
        elif (message := SlackHumanText.from_event(event)) is not None:
            await message.save()
    except Exception:
        logger.exception(
            "Recording Slack human text failed",
            extra={"slack_channel_id": event.resolve_channel_id(), "slack_message_ts": event.ts},
        )


@dataclass(frozen=True, kw_only=True)
class ThreadHumanText:
    """A message a person sent an Open SWE thread from a surface other than Slack."""

    thread_id: str
    message_id: str
    body: str
    author: Author
    sent_at: datetime

    @classmethod
    def from_message(
        cls,
        *,
        thread_id: str,
        message_id: str,
        text: str,
        sender: MessageSender | None,
        sent_at: datetime,
    ) -> Self | None:
        """What a person wrote in a human transcript message, or ``None`` when no person did.

        Platform notices, automation prompts and events ride in envelopes of
        kind ``system``, and Slack messages are recorded from Slack.
        """
        envelopes = input_envelopes(text)
        if envelopes:
            authored = [
                envelope
                for envelope in envelopes
                if envelope.kind == "human" and envelope.surface != "slack" and envelope.body
            ]
            if not authored:
                return None
            body = "\n\n".join(envelope.body for envelope in authored)
            author = Author.from_sender_id(authored[0].sender)
        elif sender is not None and sender.kind in _UNWRAPPED_SOURCES and text.strip():
            body = text.strip()
            author = Author(provider="github", key=sender.login)
        else:
            return None
        return cls(
            thread_id=thread_id, message_id=message_id, body=body, author=author, sent_at=sent_at
        )

    @classmethod
    def from_event(cls, thread_id: str, event: TranscriptEvent, sent_at: datetime) -> Self | None:
        match event:
            case TurnRequested():
                return cls.from_message(
                    thread_id=thread_id,
                    message_id=event.message_id,
                    text=event.text,
                    sender=event.sender,
                    sent_at=sent_at,
                )
            # A human message inside a subagent is the parent agent's prompt to it.
            case MessageCompleted(role="human", namespace=[]):
                return cls.from_message(
                    thread_id=thread_id,
                    message_id=event.message_id,
                    text=event.text,
                    sender=event.sender,
                    sent_at=sent_at,
                )
            case _:
                return None

    async def save(self, conn: AsyncConnection) -> bool:
        """Record this message; whether its thread is public."""
        metadata = await conn.scalar(
            text("SELECT metadata FROM thread WHERE thread_id = :thread_id"),
            {"thread_id": self.thread_id},
        )
        if not isinstance(metadata, dict) or not public_thread(metadata):
            return False
        await conn.execute(
            _SAVE_THREAD,
            {
                "id": uuid.uuid7(),
                "thread_id": self.thread_id,
                "message_id": self.message_id,
                "body": self.body,
                "sent_at": self.sent_at,
                **self.author.params(),
            },
        )
        return True


async def observe_transcript_event(
    conn: AsyncConnection, thread_id: str, event: TranscriptEvent, sent_at: datetime
) -> None:
    """Record the human writing a transcript event carries, inside the append's transaction.

    A savepoint keeps a failure here from undoing the append itself.
    """
    message = ThreadHumanText.from_event(thread_id, event, sent_at)
    if message is None:
        return
    try:
        async with conn.begin_nested():
            await message.save(conn)
    except Exception:
        logger.exception(
            "Recording thread human text failed",
            extra={"thread_id": thread_id, "message_id": message.message_id},
        )
