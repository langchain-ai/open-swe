"""The read model: one snapshot of a thread, and the log reads a subscriber needs.

Everything here reads the projections rather than folding the log, and the whole
snapshot comes out of a single ``read_only_transaction`` so the turns, messages
and tool calls a client reduces are all consistent with ``version``. LangGraph
is never consulted: ``thread.metadata`` mirrors its thread metadata precisely so
the read path can authorize a caller on its own.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.database import postgres
from agent.transcript.events import (
    JsonObject,
    MessageRole,
    StoredEvent,
    ThreadKind,
    ThreadStatus,
)

MAX_REPLAY_EVENTS = 1000
"""Replaying more events than this is slower than sending a fresh snapshot."""

MAX_REPLAY_BYTES = 8 * 1024 * 1024


class ThreadView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ThreadKind
    status: ThreadStatus
    active_run_id: str | None
    title: str | None
    created_at: datetime
    updated_at: datetime


class TurnView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: UUID
    run_id: str | None
    state: str
    requested_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None
    head_commit: str | None


class MessageView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    turn_id: UUID
    role: MessageRole
    text: str
    reasoning: str
    streaming: bool
    namespace: list[str]
    sender: JsonObject | None
    images: list[JsonValue] | None
    created_at: datetime


class ToolCallView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call_id: str
    turn_id: UUID
    message_id: str | None
    name: str
    input: JsonObject
    status: str
    output_preview: str | None
    output_truncated: bool
    has_output: bool
    namespace: list[str]
    started_at: datetime
    ended_at: datetime | None


class NoticeView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: UUID
    kind: str
    data: JsonObject


class TranscriptSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    version: int
    thread: ThreadView
    turns: list[TurnView]
    messages: list[MessageView]
    tool_calls: list[ToolCallView]
    notices: list[NoticeView]


@dataclass(frozen=True, kw_only=True)
class TranscriptAccess:
    """What the read path needs before it will serve a thread."""

    thread_id: str
    version: int
    metadata: JsonObject


@dataclass(frozen=True, kw_only=True)
class ReplayGap:
    """The distance between a subscriber's cursor and the head of the log."""

    head: int
    events: int
    payload_bytes: int

    def needs_snapshot(self, after: int) -> bool:
        return (
            after > self.head
            or self.events > MAX_REPLAY_EVENTS
            or self.payload_bytes > MAX_REPLAY_BYTES
        )


@dataclass(frozen=True, kw_only=True)
class ToolOutput:
    output: str
    truncated: bool


async def load_access(thread_id: str) -> TranscriptAccess | None:
    """The thread's mirrored metadata and head version, or ``None`` when untranscribed."""
    async with postgres.read_only_transaction() as conn:
        return await _load_access(conn, thread_id)


async def _load_access(conn: AsyncConnection, thread_id: str) -> TranscriptAccess | None:
    result = await conn.execute(
        text("SELECT thread_id, version, metadata FROM thread WHERE thread_id = :thread_id"),
        {"thread_id": thread_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    return TranscriptAccess(
        thread_id=row["thread_id"],
        version=row["version"],
        metadata=dict(row["metadata"]),
    )


async def load_snapshot(thread_id: str) -> TranscriptSnapshot | None:
    """Everything needed to render the thread, as of one consistent read."""
    async with postgres.read_only_transaction() as conn:
        thread = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT thread_id, version, kind, status, active_run_id, title,
                           created_at, updated_at
                    FROM thread WHERE thread_id = :thread_id
                    """
                    ),
                    {"thread_id": thread_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if thread is None:
            return None
        turns = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT turn_id, run_id, state, requested_at, started_at, completed_at,
                           error, head_commit
                    FROM thread_turn WHERE thread_id = :thread_id
                    ORDER BY requested_at, turn_id
                    """
                    ),
                    {"thread_id": thread_id},
                )
            )
            .mappings()
            .all()
        )
        messages = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT message_id, turn_id, role, text, reasoning, streaming, namespace,
                           sender, images, created_at
                    FROM thread_message WHERE thread_id = :thread_id
                    ORDER BY created_at, message_id
                    """
                    ),
                    {"thread_id": thread_id},
                )
            )
            .mappings()
            .all()
        )
        tool_calls = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT tool_call_id, turn_id, message_id, name, input, status,
                           output_preview, output_truncated, output IS NOT NULL AS has_output,
                           namespace, started_at, ended_at
                    FROM thread_tool_call WHERE thread_id = :thread_id
                    ORDER BY started_at, tool_call_id
                    """
                    ),
                    {"thread_id": thread_id},
                )
            )
            .mappings()
            .all()
        )
        notices = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT DISTINCT ON (payload ->> 'kind')
                           turn_id, payload ->> 'kind' AS kind, payload -> 'data' AS data
                    FROM thread_event
                    WHERE thread_id = :thread_id
                      AND event_type = 'run.notice'
                      AND turn_id = (
                          SELECT turn_id FROM thread_turn
                          WHERE thread_id = :thread_id AND state IN ('requested', 'running')
                          ORDER BY requested_at DESC, turn_id DESC
                          LIMIT 1
                      )
                    ORDER BY payload ->> 'kind', version DESC
                    """
                    ),
                    {"thread_id": thread_id},
                )
            )
            .mappings()
            .all()
        )
    return TranscriptSnapshot(
        thread_id=thread["thread_id"],
        version=thread["version"],
        thread=ThreadView(
            kind=thread["kind"],
            status=thread["status"],
            active_run_id=thread["active_run_id"],
            title=thread["title"],
            created_at=thread["created_at"],
            updated_at=thread["updated_at"],
        ),
        turns=[TurnView.model_validate(dict(turn)) for turn in turns],
        messages=[MessageView.model_validate(dict(message)) for message in messages],
        tool_calls=[ToolCallView.model_validate(dict(call)) for call in tool_calls],
        notices=[NoticeView.model_validate(dict(notice)) for notice in notices],
    )


async def measure_gap(thread_id: str, after: int) -> ReplayGap:
    """How much log stands between ``after`` and the head, in events and in bytes."""
    async with postgres.read_only_transaction() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT
                        (SELECT version FROM thread WHERE thread_id = :thread_id) AS head,
                        count(*) AS events,
                        COALESCE(sum(pg_column_size(payload)), 0) AS payload_bytes
                    FROM thread_event
                    WHERE thread_id = :thread_id AND version > :after
                    """
                    ),
                    {"thread_id": thread_id, "after": after},
                )
            )
            .mappings()
            .one()
        )
    return ReplayGap(
        head=row["head"] or 0,
        events=row["events"],
        payload_bytes=row["payload_bytes"],
    )


async def load_events(thread_id: str, *, after: int, limit: int) -> list[StoredEvent]:
    """Stored events with ``version > after``, oldest first."""
    async with postgres.read_only_transaction() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT thread_id, version, event_id, event_type, schema_version, run_id,
                           turn_id, command_id, actor_kind, occurred_at, payload
                    FROM thread_event
                    WHERE thread_id = :thread_id AND version > :after
                    ORDER BY version
                    LIMIT :limit
                    """
                    ),
                    {"thread_id": thread_id, "after": after, "limit": limit},
                )
            )
            .mappings()
            .all()
        )
    return [StoredEvent.model_validate(dict(row)) for row in rows]


async def load_tool_output(thread_id: str, tool_call_id: str) -> ToolOutput | None:
    async with postgres.read_only_transaction() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT output, output_truncated FROM thread_tool_call
                    WHERE thread_id = :thread_id AND tool_call_id = :tool_call_id
                    """
                    ),
                    {"thread_id": thread_id, "tool_call_id": tool_call_id},
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        return None
    return ToolOutput(output=row["output"] or "", truncated=bool(row["output_truncated"]))
