"""The SQL projection of one event onto the transcript read tables.

Every projection is idempotent and runs in the same transaction as the event
insert, so a reader never sees a row that the log does not explain. Where an
event accumulates (``message.appended``), the concatenation lives in the
conflict clause: the client reducer performs the same concatenation on the same
fragment, and ``message.completed`` replaces the accumulation with the
canonical text so a fragment that never arrived heals itself.
"""

import json
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import ARRAY, Text, TextClause, bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.transcript.events import (
    MessageAppended,
    MessageCompleted,
    MessageImage,
    ThreadCreated,
    ThreadMetaUpdated,
    ToolCompleted,
    ToolStarted,
    TranscriptEvent,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnRequested,
    TurnStarted,
)

MAX_TOOL_OUTPUT_CHARS = 256 * 1024
TOOL_OUTPUT_PREVIEW_CHARS = 2000


def _json(value: object) -> str | None:
    """A JSON parameter for a ``CAST(:param AS jsonb)`` placeholder."""
    if value is None:
        return None
    return json.dumps(value)


def _model_json(model: BaseModel | None) -> str | None:
    return None if model is None else model.model_dump_json()


def _models_json(models: list[MessageImage] | None) -> str | None:
    if not models:
        return None
    return json.dumps([model.model_dump(mode="json") for model in models])


def _with_namespace(sql: str) -> TextClause:
    """A statement whose ``:namespace`` parameter binds as ``text[]``."""
    return text(sql).bindparams(bindparam("namespace", type_=ARRAY(Text)))


async def ensure_thread_row(conn: AsyncConnection, thread_id: str, event: TranscriptEvent) -> None:
    """Insert the ``thread`` row a ``thread.created`` event describes.

    Runs before the event insert because ``thread_event`` references it.
    """
    if not isinstance(event, ThreadCreated):
        return
    await conn.execute(
        text(
            """
            INSERT INTO thread (thread_id, kind, version, status, title, metadata)
            VALUES (:thread_id, :kind, 0, 'idle', :title, CAST(:metadata AS jsonb))
            ON CONFLICT (thread_id) DO NOTHING
            """
        ),
        {
            "thread_id": thread_id,
            "kind": event.kind,
            "title": event.title,
            "metadata": _json(event.metadata),
        },
    )


async def apply(
    conn: AsyncConnection,
    *,
    thread_id: str,
    version: int,
    event: TranscriptEvent,
    run_id: str | None,
    occurred_at: datetime,
) -> None:
    """Project ``event`` onto the read tables.

    ``thread.created`` is already applied by :func:`ensure_thread_row`, and
    ``run.notice`` has no projection — the snapshot reads notices from the log.
    """
    match event:
        case ThreadCreated():
            return
        case ThreadMetaUpdated():
            await _meta_updated(conn, thread_id, event)
        case TurnRequested():
            await _turn_requested(conn, thread_id, version, event, occurred_at)
        case TurnStarted():
            await _turn_started(conn, thread_id, event, occurred_at)
        case TurnCompleted():
            await _turn_completed(conn, thread_id, version, event, run_id, occurred_at)
        case TurnFailed():
            await _turn_failed(conn, thread_id, event, run_id, occurred_at)
        case TurnInterrupted():
            await _turn_interrupted(conn, thread_id, event, run_id, occurred_at)
        case MessageAppended():
            await _message_appended(conn, thread_id, version, event, occurred_at)
        case MessageCompleted():
            await _message_completed(conn, thread_id, version, event)
        case ToolStarted():
            await _tool_started(conn, thread_id, version, event, occurred_at)
        case ToolCompleted():
            await _tool_completed(conn, thread_id, version, event, occurred_at)
        case _:
            return


async def _meta_updated(conn: AsyncConnection, thread_id: str, event: ThreadMetaUpdated) -> None:
    patch = event.patch
    assignments = ["updated_at = clock_timestamp()"]
    params: dict[str, object] = {"thread_id": thread_id}
    if patch.title is not None:
        assignments.append("title = :title")
        params["title"] = patch.title
    if patch.status is not None:
        assignments.append("status = :status")
        params["status"] = patch.status
    if "active_run_id" in patch.model_fields_set:
        assignments.append("active_run_id = :active_run_id")
        params["active_run_id"] = patch.active_run_id
    if patch.metadata is not None:
        assignments.append("metadata = metadata || CAST(:metadata AS jsonb)")
        params["metadata"] = _json(patch.metadata)
    await conn.execute(
        text(f"UPDATE thread SET {', '.join(assignments)} WHERE thread_id = :thread_id"),
        params,
    )


async def _turn_requested(
    conn: AsyncConnection,
    thread_id: str,
    version: int,
    event: TurnRequested,
    occurred_at: datetime,
) -> None:
    await conn.execute(
        text(
            """
            INSERT INTO thread_turn (turn_id, thread_id, state, requested_at)
            VALUES (:turn_id, :thread_id, 'requested', :requested_at)
            ON CONFLICT (turn_id) DO NOTHING
            """
        ),
        {"turn_id": event.turn_id, "thread_id": thread_id, "requested_at": occurred_at},
    )
    await conn.execute(
        _with_namespace(
            """
            INSERT INTO thread_message (
                message_id, thread_id, turn_id, version, role, text, reasoning,
                streaming, namespace, sender, images, created_at
            )
            VALUES (
                :message_id, :thread_id, :turn_id, :version, 'human', :text, '',
                false, :namespace, CAST(:sender AS jsonb), CAST(:images AS jsonb), :created_at
            )
            ON CONFLICT (thread_id, message_id) DO NOTHING
            """
        ),
        {
            "message_id": event.message_id,
            "thread_id": thread_id,
            "turn_id": event.turn_id,
            "version": version,
            "text": event.text,
            "namespace": [],
            "sender": _model_json(event.sender),
            "images": _models_json(event.images),
            "created_at": occurred_at,
        },
    )


async def _turn_started(
    conn: AsyncConnection, thread_id: str, event: TurnStarted, occurred_at: datetime
) -> None:
    # Upserted rather than updated: a run triggered outside the dashboard has no
    # ``turn.requested`` ahead of it, and its turn still has to exist.
    await conn.execute(
        text(
            """
            INSERT INTO thread_turn (turn_id, thread_id, run_id, state, requested_at, started_at)
            VALUES (:turn_id, :thread_id, :run_id, 'running', :started_at, :started_at)
            ON CONFLICT (turn_id) DO UPDATE SET
                state = 'running',
                run_id = EXCLUDED.run_id,
                started_at = COALESCE(thread_turn.started_at, EXCLUDED.started_at)
            WHERE thread_turn.thread_id = EXCLUDED.thread_id
            """
        ),
        {
            "turn_id": event.turn_id,
            "thread_id": thread_id,
            "run_id": event.run_id,
            "started_at": occurred_at,
        },
    )
    await conn.execute(
        text(
            """
            UPDATE thread
            SET status = 'running', active_run_id = :run_id, updated_at = clock_timestamp()
            WHERE thread_id = :thread_id
            """
        ),
        {"thread_id": thread_id, "run_id": event.run_id},
    )


async def _turn_completed(
    conn: AsyncConnection,
    thread_id: str,
    version: int,
    event: TurnCompleted,
    run_id: str | None,
    occurred_at: datetime,
) -> None:
    await conn.execute(
        text(
            """
            UPDATE thread_turn SET
                state = 'completed',
                run_id = COALESCE(:run_id, run_id),
                completed_at = :completed_at,
                base_commit = COALESCE(:base_commit, base_commit),
                head_commit = COALESCE(:head_commit, head_commit),
                changed_files = COALESCE(CAST(:changed_files AS jsonb), changed_files)
            WHERE turn_id = :turn_id AND thread_id = :thread_id
              AND state IN ('requested', 'running')
            """
        ),
        {
            "thread_id": thread_id,
            "turn_id": event.turn_id,
            "run_id": event.run_id or run_id,
            "completed_at": occurred_at,
            "base_commit": event.base_commit,
            "head_commit": event.head_commit,
            "changed_files": _json(event.changed_files),
        },
    )
    await _settle_thread(conn, thread_id, status="idle")
    await conn.execute(
        text(
            """
            UPDATE thread_message SET streaming = false, version = :version
            WHERE thread_id = :thread_id AND turn_id = :turn_id AND streaming
            """
        ),
        {"thread_id": thread_id, "turn_id": event.turn_id, "version": version},
    )


async def _turn_failed(
    conn: AsyncConnection,
    thread_id: str,
    event: TurnFailed,
    run_id: str | None,
    occurred_at: datetime,
) -> None:
    await conn.execute(
        text(
            """
            UPDATE thread_turn SET
                state = 'failed',
                run_id = COALESCE(:run_id, run_id),
                completed_at = :completed_at,
                error = :error
            WHERE turn_id = :turn_id AND thread_id = :thread_id
              AND state IN ('requested', 'running')
            """
        ),
        {
            "thread_id": thread_id,
            "turn_id": event.turn_id,
            "run_id": event.run_id or run_id,
            "completed_at": occurred_at,
            "error": event.error,
        },
    )
    await _settle_thread(conn, thread_id, status="error")


async def _turn_interrupted(
    conn: AsyncConnection,
    thread_id: str,
    event: TurnInterrupted,
    run_id: str | None,
    occurred_at: datetime,
) -> None:
    await conn.execute(
        text(
            """
            UPDATE thread_turn SET
                state = 'interrupted',
                run_id = COALESCE(:run_id, run_id),
                completed_at = :completed_at
            WHERE turn_id = :turn_id AND thread_id = :thread_id
              AND state IN ('requested', 'running')
            """
        ),
        {
            "thread_id": thread_id,
            "turn_id": event.turn_id,
            "run_id": event.run_id or run_id,
            "completed_at": occurred_at,
        },
    )
    await _settle_thread(conn, thread_id, status="idle")


async def _settle_thread(conn: AsyncConnection, thread_id: str, *, status: str) -> None:
    await conn.execute(
        text(
            """
            UPDATE thread
            SET status = :status, active_run_id = NULL, updated_at = clock_timestamp()
            WHERE thread_id = :thread_id
            """
        ),
        {"thread_id": thread_id, "status": status},
    )


async def _ensure_turn(
    conn: AsyncConnection, thread_id: str, turn_id: UUID, occurred_at: datetime
) -> None:
    """The turn a message or tool call belongs to, materialised if it is missing.

    The turn row is what a windowed read pages over, so a message whose turn
    was never announced — a run started outside the dashboard, a replay that
    begins mid-turn — would otherwise be invisible rather than merely
    unlabelled. ``requested_at`` falls back to the event's own time, which is
    the same anchor the client's reducer invents for an unseen turn.
    """
    await conn.execute(
        text(
            """
            INSERT INTO thread_turn (turn_id, thread_id, state, requested_at)
            VALUES (:turn_id, :thread_id, 'running', :requested_at)
            ON CONFLICT (turn_id) DO NOTHING
            """
        ),
        {"turn_id": turn_id, "thread_id": thread_id, "requested_at": occurred_at},
    )


async def _message_appended(
    conn: AsyncConnection,
    thread_id: str,
    version: int,
    event: MessageAppended,
    occurred_at: datetime,
) -> None:
    await _ensure_turn(conn, thread_id, event.turn_id, occurred_at)
    await conn.execute(
        _with_namespace(
            """
            INSERT INTO thread_message (
                message_id, thread_id, turn_id, version, role, text, reasoning,
                streaming, namespace, created_at
            )
            VALUES (
                :message_id, :thread_id, :turn_id, :version, 'ai', :text, :reasoning,
                true, :namespace, :created_at
            )
            ON CONFLICT (thread_id, message_id) DO UPDATE SET
                version = EXCLUDED.version,
                text = thread_message.text || EXCLUDED.text,
                reasoning = thread_message.reasoning || EXCLUDED.reasoning,
                streaming = true
            """
        ),
        {
            "message_id": event.message_id,
            "thread_id": thread_id,
            "turn_id": event.turn_id,
            "version": version,
            "text": event.text or "",
            "reasoning": event.reasoning or "",
            "namespace": list(event.namespace),
            "created_at": occurred_at,
        },
    )


async def _message_completed(
    conn: AsyncConnection, thread_id: str, version: int, event: MessageCompleted
) -> None:
    await _ensure_turn(conn, thread_id, event.turn_id, event.created_at)
    await conn.execute(
        _with_namespace(
            """
            INSERT INTO thread_message (
                message_id, thread_id, turn_id, version, role, text, reasoning,
                streaming, namespace, sender, images, usage, created_at
            )
            VALUES (
                :message_id, :thread_id, :turn_id, :version, :role, :text, :reasoning,
                false, :namespace, CAST(:sender AS jsonb), CAST(:images AS jsonb),
                CAST(:usage AS jsonb), :created_at
            )
            ON CONFLICT (thread_id, message_id) DO UPDATE SET
                version = EXCLUDED.version,
                role = EXCLUDED.role,
                text = EXCLUDED.text,
                reasoning = EXCLUDED.reasoning,
                streaming = false,
                namespace = EXCLUDED.namespace,
                sender = COALESCE(EXCLUDED.sender, thread_message.sender),
                images = COALESCE(EXCLUDED.images, thread_message.images),
                usage = COALESCE(EXCLUDED.usage, thread_message.usage)
            """
        ),
        {
            "message_id": event.message_id,
            "thread_id": thread_id,
            "turn_id": event.turn_id,
            "version": version,
            "role": event.role,
            "text": event.text,
            "reasoning": event.reasoning,
            "namespace": list(event.namespace),
            "sender": _model_json(event.sender),
            "images": _models_json(event.images),
            "usage": _model_json(event.usage),
            "created_at": event.created_at,
        },
    )


async def _tool_started(
    conn: AsyncConnection,
    thread_id: str,
    version: int,
    event: ToolStarted,
    occurred_at: datetime,
) -> None:
    await _ensure_turn(conn, thread_id, event.turn_id, occurred_at)
    await conn.execute(
        _with_namespace(
            """
            INSERT INTO thread_tool_call (
                tool_call_id, thread_id, turn_id, message_id, version, name, input,
                status, namespace, started_at
            )
            VALUES (
                :tool_call_id, :thread_id, :turn_id, :message_id, :version, :name,
                CAST(:input AS jsonb), 'in_progress', :namespace, :started_at
            )
            ON CONFLICT (thread_id, tool_call_id) DO UPDATE SET
                version = EXCLUDED.version,
                message_id = COALESCE(EXCLUDED.message_id, thread_tool_call.message_id),
                name = EXCLUDED.name,
                input = EXCLUDED.input,
                namespace = EXCLUDED.namespace
            """
        ),
        {
            "tool_call_id": event.tool_call_id,
            "thread_id": thread_id,
            "turn_id": event.turn_id,
            "message_id": event.message_id,
            "version": version,
            "name": event.name,
            "input": _json(event.input),
            "namespace": list(event.namespace),
            "started_at": occurred_at,
        },
    )


async def _tool_completed(
    conn: AsyncConnection,
    thread_id: str,
    version: int,
    event: ToolCompleted,
    occurred_at: datetime,
) -> None:
    output = event.output[:MAX_TOOL_OUTPUT_CHARS]
    truncated = event.output_truncated or len(output) < len(event.output)
    await conn.execute(
        text(
            """
            UPDATE thread_tool_call SET
                version = :version,
                status = :status,
                output = :output,
                output_preview = :output_preview,
                output_truncated = :output_truncated,
                ended_at = :ended_at
            WHERE tool_call_id = :tool_call_id AND thread_id = :thread_id
            """
        ),
        {
            "tool_call_id": event.tool_call_id,
            "thread_id": thread_id,
            "version": version,
            "status": event.status,
            "output": output,
            "output_preview": output[:TOOL_OUTPUT_PREVIEW_CHARS],
            "output_truncated": truncated,
            "ended_at": occurred_at,
        },
    )
