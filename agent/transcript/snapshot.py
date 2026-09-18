"""The read model: one snapshot of a thread, and the log reads a subscriber needs.

Everything here reads the projections rather than folding the log, and the whole
snapshot comes out of a single ``snapshot_transaction`` — REPEATABLE READ, so
every statement sees one database snapshot and the turns, messages and tool
calls a client reduces are all consistent with ``version``. LangGraph
is never consulted: ``thread.metadata`` mirrors its thread metadata precisely so
the read path can authorize a caller on its own.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import ARRAY, DateTime, RowMapping, TextClause, Uuid, bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.database import postgres
from agent.transcript.cursor import TurnPageCursor, encode_turn_cursor
from agent.transcript.events import (
    CheckpointFile,
    CheckpointStatus,
    JsonObject,
    MessageRole,
    StoredEvent,
    ThreadStatus,
)

MAX_REPLAY_EVENTS = 1000
"""Replaying more events than this is slower than sending a fresh snapshot."""

MAX_REPLAY_BYTES = 8 * 1024 * 1024

TURN_PAGE_SIZE = 40
"""Turns in one window.

Measured against our own threads: a turn is one user request and the agent's
answer, and the overwhelming majority of threads never reach forty of them, so
the default window is the whole conversation for almost every reader while the
long tail — a scheduled thread that has been answering for weeks — stops
costing a megabyte of tool-call previews on first paint.
"""

MAX_TURN_PAGE_SIZE = 200
"""A client may ask for a larger page, but not for the whole log at once."""


class ThreadView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ThreadStatus
    title: str | None
    created_at: datetime
    updated_at: datetime


class CheckpointView(BaseModel):
    """The commit a turn left behind, as ``turn.checkpoint.completed`` recorded it."""

    model_config = ConfigDict(extra="forbid")

    checkpoint_turn_count: int
    checkpoint_ref: str
    commit: str | None
    status: CheckpointStatus
    files: list[CheckpointFile]
    assistant_message_id: str | None
    error: str | None


class TurnView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: UUID
    run_id: str | None
    state: str
    requested_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None
    checkpoint: CheckpointView | None


class MessageView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    turn_id: UUID
    role: MessageRole
    text: str
    reasoning: str
    namespace: list[str]
    sender: JsonObject | None
    images: list[JsonValue] | None
    usage: JsonObject | None
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
    """The newest window of a thread, as of ``version``.

    ``version`` is the head of the log at read time whatever the window holds:
    live events only ever concern the newest turn or the thread row, so a
    subscription resumed with ``after=version`` stays correct for a windowed
    read. ``older_cursor`` is ``None`` once the window reaches the first turn.
    """

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    version: int
    thread: ThreadView
    turns: list[TurnView]
    messages: list[MessageView]
    tool_calls: list[ToolCallView]
    notices: list[NoticeView]
    older_cursor: str | None


class TranscriptTurnPage(BaseModel):
    """One page of turns strictly older than the cursor that asked for it.

    No notices and no thread row: both describe the newest turn, which an older
    page by definition does not hold.
    """

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    turns: list[TurnView]
    messages: list[MessageView]
    tool_calls: list[ToolCallView]
    older_cursor: str | None


@dataclass(frozen=True, kw_only=True)
class ReplayGap:
    """The distance between a subscriber's cursor and the head of the log.

    ``head`` is ``None`` for a thread with no transcript — deleted, or never
    transcribed — which a subscriber has to be told about rather than waiting
    out in silence.
    """

    head: int | None
    events: int
    payload_bytes: int

    @property
    def needs_snapshot(self) -> bool:
        return self.events > MAX_REPLAY_EVENTS or self.payload_bytes > MAX_REPLAY_BYTES


async def _rows(
    conn: AsyncConnection, statement: str | TextClause, parameters: Mapping[str, object]
) -> list[RowMapping]:
    """The mapped rows of one statement, without the ``execute`` unwrapping noise."""
    result = await conn.execute(
        text(statement) if isinstance(statement, str) else statement, parameters
    )
    return list(result.mappings())


async def _row(
    conn: AsyncConnection, statement: str | TextClause, parameters: Mapping[str, object]
) -> RowMapping | None:
    rows = await _rows(conn, statement, parameters)
    return rows[0] if rows else None


async def load_access(thread_id: str) -> JsonObject | None:
    """The thread's mirrored LangGraph metadata, or ``None`` when untranscribed.

    This is what the read path authorizes a caller against, and its absence is
    what says the thread is not served by the transcript API at all.
    """
    async with postgres.snapshot_transaction() as conn:
        row = await _row(
            conn,
            "SELECT metadata FROM thread WHERE thread_id = :thread_id",
            {"thread_id": thread_id},
        )
    return None if row is None else dict(row["metadata"])


# The checkpoint is joined in rather than fetched per page: it is one row per
# turn, and every reader that wants a turn wants what it changed.
_TURN_COLUMNS = """
    turn.turn_id, turn.run_id, turn.state, turn.requested_at, turn.started_at,
    turn.completed_at, turn.error,
    checkpoint.checkpoint_turn_count, checkpoint.checkpoint_ref, checkpoint.commit,
    checkpoint.status AS checkpoint_status, checkpoint.files,
    checkpoint.assistant_message_id, checkpoint.error AS checkpoint_error
"""


def _turn_view(row: RowMapping) -> TurnView:
    columns = dict(row)
    checkpoint = {
        "checkpoint_turn_count": columns.pop("checkpoint_turn_count"),
        "checkpoint_ref": columns.pop("checkpoint_ref"),
        "commit": columns.pop("commit"),
        "status": columns.pop("checkpoint_status"),
        "files": columns.pop("files"),
        "assistant_message_id": columns.pop("assistant_message_id"),
        "error": columns.pop("checkpoint_error"),
    }
    return TurnView(
        **columns,
        checkpoint=(
            None
            if checkpoint["checkpoint_ref"] is None
            else CheckpointView.model_validate(checkpoint)
        ),
    )


_MESSAGE_COLUMNS = """
    message_id, turn_id, role, text, reasoning, namespace,
    sender, images, usage, created_at
"""

_TOOL_CALL_COLUMNS = """
    tool_call_id, turn_id, message_id, name, input, status,
    output_preview, output_truncated, output IS NOT NULL AS has_output,
    namespace, started_at, ended_at
"""


def _page_size(limit: int | None) -> int:
    return max(1, min(limit or TURN_PAGE_SIZE, MAX_TURN_PAGE_SIZE))


async def _load_turns(
    conn: AsyncConnection,
    thread_id: str,
    *,
    before: TurnPageCursor | None,
    limit: int,
) -> tuple[list[TurnView], str | None]:
    """The newest ``limit`` turns before the cursor, oldest first, and the next cursor.

    The scan walks ``(thread_id, requested_at, turn_id)`` backwards and reads
    one row past the page, which is what says whether an older page exists
    without a second query. The page is reversed on the way out so callers see
    the same ``(requested_at, turn_id)`` order the unwindowed read produced.
    """
    bound = "" if before is None else " AND (requested_at, turn_id) < (:before_at, :before_turn)"
    statement = text(
        f"""
        SELECT {_TURN_COLUMNS}
        FROM thread_turn AS turn
        LEFT JOIN thread_turn_checkpoint AS checkpoint USING (thread_id, turn_id)
        WHERE thread_id = :thread_id{bound}
        ORDER BY requested_at DESC, turn_id DESC
        LIMIT :limit
        """
    )
    parameters: dict[str, object] = {"thread_id": thread_id, "limit": limit + 1}
    if before is not None:
        statement = statement.bindparams(
            bindparam("before_at", type_=DateTime(timezone=True)),
            bindparam("before_turn", type_=Uuid),
        )
        parameters["before_at"] = before.before_requested_at
        parameters["before_turn"] = before.before_turn_id
    rows = await _rows(conn, statement, parameters)
    turns = [_turn_view(row) for row in reversed(rows[:limit])]
    if len(rows) <= limit or not turns:
        return turns, None
    oldest = turns[0]
    return turns, encode_turn_cursor(
        TurnPageCursor(
            thread_id=thread_id,
            before_requested_at=oldest.requested_at,
            before_turn_id=oldest.turn_id,
        )
    )


async def _load_turn_contents(
    conn: AsyncConnection,
    thread_id: str,
    turn_ids: list[UUID],
) -> tuple[list[MessageView], list[ToolCallView]]:
    """Every message and tool call belonging to the turns of one page."""
    if not turn_ids:
        return [], []
    parameters: dict[str, object] = {"thread_id": thread_id, "turn_ids": turn_ids}
    turn_ids_param = bindparam("turn_ids", type_=ARRAY(Uuid))
    messages = await _rows(
        conn,
        text(
            f"""
            SELECT {_MESSAGE_COLUMNS}
            FROM thread_message
            WHERE thread_id = :thread_id AND turn_id = ANY(:turn_ids)
            ORDER BY created_at, message_id
            """
        ).bindparams(turn_ids_param),
        parameters,
    )
    tool_calls = await _rows(
        conn,
        text(
            f"""
            SELECT {_TOOL_CALL_COLUMNS}
            FROM thread_tool_call
            WHERE thread_id = :thread_id AND turn_id = ANY(:turn_ids)
            ORDER BY started_at, tool_call_id
            """
        ).bindparams(turn_ids_param),
        parameters,
    )
    return (
        [MessageView.model_validate(dict(message)) for message in messages],
        [ToolCallView.model_validate(dict(call)) for call in tool_calls],
    )


async def load_snapshot(thread_id: str, *, limit: int | None = None) -> TranscriptSnapshot | None:
    """The newest window of the thread, as of one consistent read.

    Older turns are reached through ``older_cursor`` and
    :func:`load_turn_page`; they are settled and immutable, so a client that
    already holds them keeps them rather than refetching.
    """
    page_size = _page_size(limit)
    async with postgres.snapshot_transaction() as conn:
        thread = await _row(
            conn,
            """
            SELECT thread_id, version, status, title, created_at, updated_at
            FROM thread WHERE thread_id = :thread_id
            """,
            {"thread_id": thread_id},
        )
        if thread is None:
            return None
        turns, older_cursor = await _load_turns(conn, thread_id, before=None, limit=page_size)
        messages, tool_calls = await _load_turn_contents(
            conn, thread_id, [turn.turn_id for turn in turns]
        )
        notices = await _rows(
            conn,
            """
            WITH newest_turn AS (
                SELECT turn_id, state FROM thread_turn
                WHERE thread_id = :thread_id
                ORDER BY requested_at DESC, turn_id DESC
                LIMIT 1
            )
            SELECT DISTINCT ON (event.payload ->> 'kind')
                   event.turn_id,
                   event.payload ->> 'kind' AS kind,
                   event.payload -> 'data' AS data
            FROM thread_event AS event
            JOIN newest_turn ON newest_turn.turn_id = event.turn_id
            WHERE event.thread_id = :thread_id
              AND event.event_type = 'run.notice'
              -- Offloading describes what a run is doing right now, so it
              -- dies with its turn; routing describes how the turn was
              -- executed and outlives it.
              AND (
                  event.payload ->> 'kind' <> 'conversation_offloading'
                  OR newest_turn.state IN ('requested', 'running')
              )
            ORDER BY event.payload ->> 'kind', event.version DESC
            """,
            {"thread_id": thread_id},
        )
    return TranscriptSnapshot(
        thread_id=thread["thread_id"],
        version=thread["version"],
        thread=ThreadView(
            status=thread["status"],
            title=thread["title"],
            created_at=thread["created_at"],
            updated_at=thread["updated_at"],
        ),
        turns=turns,
        messages=messages,
        tool_calls=tool_calls,
        notices=[NoticeView.model_validate(dict(notice)) for notice in notices],
        older_cursor=older_cursor,
    )


async def load_turn_page(
    thread_id: str, *, before: TurnPageCursor, limit: int | None = None
) -> TranscriptTurnPage:
    """The page of turns immediately older than ``before``.

    Disjoint from and adjacent to the page the cursor came from: the bound is
    exclusive and the ordering is the snapshot's, so paging the whole way back
    visits every turn exactly once.
    """
    page_size = _page_size(limit)
    async with postgres.snapshot_transaction() as conn:
        turns, older_cursor = await _load_turns(conn, thread_id, before=before, limit=page_size)
        messages, tool_calls = await _load_turn_contents(
            conn, thread_id, [turn.turn_id for turn in turns]
        )
    return TranscriptTurnPage(
        thread_id=thread_id,
        turns=turns,
        messages=messages,
        tool_calls=tool_calls,
        older_cursor=older_cursor,
    )


async def measure_gap(thread_id: str, after: int) -> ReplayGap:
    """How much log stands between ``after`` and the head, in events and in bytes."""
    async with postgres.snapshot_transaction() as conn:
        (row,) = await _rows(
            conn,
            """
            SELECT
                (SELECT version FROM thread WHERE thread_id = :thread_id) AS head,
                count(*) AS events,
                COALESCE(sum(pg_column_size(payload)), 0) AS payload_bytes
            FROM thread_event
            WHERE thread_id = :thread_id AND version > :after
            """,
            {"thread_id": thread_id, "after": after},
        )
    return ReplayGap(
        head=row["head"],
        events=row["events"],
        payload_bytes=row["payload_bytes"],
    )


async def load_events(thread_id: str, *, after: int, limit: int) -> list[StoredEvent]:
    """Stored events with ``version > after``, oldest first."""
    async with postgres.snapshot_transaction() as conn:
        rows = await _rows(
            conn,
            """
            SELECT thread_id, version, event_id, event_type, schema_version, run_id,
                   turn_id, command_id, actor_kind, occurred_at, payload
            FROM thread_event
            WHERE thread_id = :thread_id AND version > :after
            ORDER BY version
            LIMIT :limit
            """,
            {"thread_id": thread_id, "after": after, "limit": limit},
        )
    return [StoredEvent.model_validate(dict(row)) for row in rows]


async def load_tool_output(thread_id: str, tool_call_id: str) -> str | None:
    """The stored output of one tool call, or ``None`` when there is no such call."""
    async with postgres.snapshot_transaction() as conn:
        row = await _row(
            conn,
            """
            SELECT output FROM thread_tool_call
            WHERE thread_id = :thread_id AND tool_call_id = :tool_call_id
            """,
            {"thread_id": thread_id, "tool_call_id": tool_call_id},
        )
    return None if row is None else row["output"] or ""
