"""The SQL projection of one event onto the transcript read tables.

Every projection is idempotent and runs in the same transaction as the event
insert, so a reader never sees a row that the log does not explain. Where an
event accumulates (``message.appended``), the concatenation lives in the
conflict clause: the client reducer performs the same concatenation on the same
fragment, and ``message.completed`` replaces the accumulation with the
canonical text so a fragment that never arrived heals itself.

Every projection is a function of the log and the blob tables beside it —
``thread_attachment`` and ``thread_tool_output`` — and of nothing else, which
is what lets :mod:`agent.transcript.rebuild` throw a thread's read tables away
and fold them back out of its events.
"""

import json
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import ARRAY, Text, TextClause, bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.threads.index import ThreadIndexStatus, mark_thread_index_status
from agent.transcript.events import (
    MessageAppended,
    MessageAttachment,
    MessageCompleted,
    ThreadCreated,
    ThreadMetaUpdated,
    ToolCompleted,
    ToolStarted,
    TranscriptEvent,
    TurnCheckpointCompleted,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnQueued,
    TurnRequested,
    TurnStarted,
)


def _json(value: object) -> str | None:
    """A JSON parameter for a ``CAST(:param AS jsonb)`` placeholder."""
    if value is None:
        return None
    return json.dumps(value)


def _model_json(model: BaseModel | None) -> str | None:
    return None if model is None else model.model_dump_json()


def _models_json(models: list[MessageAttachment] | None) -> str | None:
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
            INSERT INTO thread (thread_id, version, status, title, metadata)
            VALUES (:thread_id, 0, 'idle', :title, CAST(:metadata AS jsonb))
            ON CONFLICT (thread_id) DO NOTHING
            """
        ),
        {
            "thread_id": thread_id,
            "title": event.title,
            "metadata": _json(event.metadata),
        },
    )


async def reset_thread_row(conn: AsyncConnection, thread_id: str, event: ThreadCreated) -> None:
    """Put the ``thread`` row back to what ``thread.created`` alone implies.

    :func:`ensure_thread_row` only ever inserts, so a rebuild that is about to
    replay the log needs the row itself wound back: everything later events say
    about the title, the metadata and the status is then reapplied by the
    replay. ``version`` is left alone — it is the head of the log, not a
    projection of it.
    """
    await conn.execute(
        text(
            """
            UPDATE thread SET
                status = 'idle',
                title = :title,
                metadata = CAST(:metadata AS jsonb),
                updated_at = clock_timestamp()
            WHERE thread_id = :thread_id
            """
        ),
        {"thread_id": thread_id, "title": event.title, "metadata": _json(event.metadata)},
    )


async def resolve(conn: AsyncConnection, thread_id: str, event: TranscriptEvent) -> TranscriptEvent:
    """The event as it will be stored, with identity only the log can settle.

    A ``turn.checkpoint.completed`` is written without an ordinal: it is
    assigned here, inside the append transaction under the thread's lock, so
    the stored event, the projection and every reader agree on it. A turn
    checkpointed a second time keeps the number it already has.
    """
    if not isinstance(event, TurnCheckpointCompleted):
        return event
    result = await conn.execute(
        text(
            """
            SELECT COALESCE(
                (SELECT checkpoint_turn_count FROM thread_turn_checkpoint
                 WHERE thread_id = :thread_id AND turn_id = :turn_id),
                (SELECT COALESCE(max(checkpoint_turn_count), 0) + 1
                 FROM thread_turn_checkpoint WHERE thread_id = :thread_id)
            )
            """
        ),
        {"thread_id": thread_id, "turn_id": event.turn_id},
    )
    return event.model_copy(update={"checkpoint_turn_count": result.scalar_one()})


async def apply(
    conn: AsyncConnection,
    *,
    thread_id: str,
    version: int,
    event: TranscriptEvent,
    run_id: str | None,
    occurred_at: datetime,
    live: bool = True,
) -> None:
    """Project ``event`` onto the read tables.

    ``thread.created`` is already applied by :func:`ensure_thread_row`, and
    ``run.notice`` has no projection — the snapshot reads notices from the log.

    ``live`` is false on a rebuild, which leaves ``thread_index`` alone: it is
    not a transcript read table, and replaying old turns onto it would stamp
    their time as the thread's latest activity and mark the thread unread.
    """
    match event:
        case ThreadCreated():
            return
        case ThreadMetaUpdated():
            await _meta_updated(conn, thread_id, event)
        case TurnRequested():
            await _turn_requested(conn, thread_id, version, event, occurred_at, live=live)
        case TurnStarted():
            await _turn_started(conn, thread_id, event, occurred_at, live=live)
        case TurnQueued():
            await _turn_queued(conn, thread_id, event)
        case TurnCompleted() | TurnFailed() | TurnInterrupted():
            await _turn_ended(conn, thread_id, version, event, run_id, occurred_at, live=live)
        case TurnCheckpointCompleted():
            await _turn_checkpoint(conn, thread_id, event, occurred_at)
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
    *,
    live: bool,
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
    # The thread is busy from the moment a turn is asked for: the run that will
    # serve it does not exist yet, and a reader must not see the thread idle.
    await _set_thread_status(conn, thread_id, status="running")
    if live:
        await mark_thread_index_status(conn, thread_id, status="running", run_id=None)
    await conn.execute(
        _with_namespace(
            """
            INSERT INTO thread_message (
                message_id, thread_id, turn_id, version, role, text, reasoning,
                namespace, sender, attachments, created_at
            )
            VALUES (
                :message_id, :thread_id, :turn_id, :version, 'human', :text, '',
                :namespace, CAST(:sender AS jsonb), CAST(:attachments AS jsonb), :created_at
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
            "attachments": _models_json(event.attachments),
            "created_at": occurred_at,
        },
    )


async def _turn_queued(conn: AsyncConnection, thread_id: str, event: TurnQueued) -> None:
    # Only a turn still waiting takes the run id: a queued turn that already
    # started or was cancelled keeps what those events recorded.
    await conn.execute(
        text(
            """
            UPDATE thread_turn SET run_id = :run_id
            WHERE turn_id = :turn_id AND thread_id = :thread_id AND state = 'requested'
            """
        ),
        {"turn_id": event.turn_id, "thread_id": thread_id, "run_id": event.run_id},
    )


async def _turn_started(
    conn: AsyncConnection,
    thread_id: str,
    event: TurnStarted,
    occurred_at: datetime,
    *,
    live: bool,
) -> None:
    # Upserted rather than updated: a run triggered outside the dashboard has no
    # ``turn.requested`` ahead of it, and its turn still has to exist. Only an
    # open turn is started, though: a ``turn.started`` that lands after the turn
    # was cancelled must not reopen it, nor mark the thread busy again.
    result = await conn.execute(
        text(
            """
            INSERT INTO thread_turn (turn_id, thread_id, run_id, state, requested_at, started_at)
            VALUES (:turn_id, :thread_id, :run_id, 'running', :started_at, :started_at)
            ON CONFLICT (turn_id) DO UPDATE SET
                state = 'running',
                run_id = EXCLUDED.run_id,
                started_at = COALESCE(thread_turn.started_at, EXCLUDED.started_at)
            WHERE thread_turn.thread_id = EXCLUDED.thread_id
              AND thread_turn.state IN ('requested', 'running')
            RETURNING turn_id
            """
        ),
        {
            "turn_id": event.turn_id,
            "thread_id": thread_id,
            "run_id": event.run_id,
            "started_at": occurred_at,
        },
    )
    if result.scalar_one_or_none() is not None:
        await _set_thread_status(conn, thread_id, status="running")
        if live:
            await mark_thread_index_status(conn, thread_id, status="running", run_id=event.run_id)


async def _turn_checkpoint(
    conn: AsyncConnection,
    thread_id: str,
    event: TurnCheckpointCompleted,
    occurred_at: datetime,
) -> None:
    """Record the turn's checkpoint, overwriting a weaker earlier attempt.

    A turn is checkpointed once, but two writers may try: the middleware at the
    end of the run and ``agent.transcript.turns`` when the run died without it.
    The row keeps whichever attempt actually produced a commit.

    ``checkpoint_turn_count`` is already resolved by :func:`resolve` under the
    thread's lock, so the unique index over it cannot be violated by anything
    that went through ``append``; a violation here means a writer bypassed it,
    and the error propagates.
    """
    statement = text(
        """
            INSERT INTO thread_turn_checkpoint (
                thread_id, turn_id, checkpoint_turn_count, checkpoint_ref, commit,
                status, files, assistant_message_id, error, completed_at
            )
            VALUES (
                :thread_id, :turn_id, :checkpoint_turn_count, :checkpoint_ref, :commit,
                :status, CAST(:files AS jsonb), :assistant_message_id, :error, :completed_at
            )
            ON CONFLICT (thread_id, turn_id) DO UPDATE SET
                checkpoint_turn_count = EXCLUDED.checkpoint_turn_count,
                checkpoint_ref = EXCLUDED.checkpoint_ref,
                commit = EXCLUDED.commit,
                status = EXCLUDED.status,
                files = EXCLUDED.files,
                assistant_message_id = COALESCE(
                    EXCLUDED.assistant_message_id, thread_turn_checkpoint.assistant_message_id
                ),
                error = EXCLUDED.error,
                completed_at = EXCLUDED.completed_at
            WHERE thread_turn_checkpoint.commit IS NULL
            """
    )
    parameters: dict[str, object] = {
        "thread_id": thread_id,
        "turn_id": event.turn_id,
        "checkpoint_turn_count": event.checkpoint_turn_count,
        "checkpoint_ref": event.checkpoint_ref,
        "commit": event.commit,
        "status": event.status,
        "files": json.dumps([file.model_dump(mode="json") for file in event.files]),
        "assistant_message_id": event.assistant_message_id,
        "error": event.error,
        "completed_at": occurred_at,
    }
    await conn.execute(statement, parameters)


async def _turn_ended(
    conn: AsyncConnection,
    thread_id: str,
    version: int,
    event: TurnCompleted | TurnFailed | TurnInterrupted,
    run_id: str | None,
    occurred_at: datetime,
    *,
    live: bool,
) -> None:
    """Close a turn, and settle the thread's status if this closed it.

    Only an open turn is ever moved, so a late completion of a turn that was
    already interrupted changes nothing — including the thread's status, which
    by then belongs to whatever happened after the interruption. A thread with
    another turn still open stays ``running``.
    """
    failed = isinstance(event, TurnFailed)
    completed = isinstance(event, TurnCompleted)
    ended_run_id = event.run_id or run_id
    result = await conn.execute(
        text(
            """
            UPDATE thread_turn SET
                state = :state,
                run_id = COALESCE(:run_id, run_id),
                completed_at = :completed_at,
                error = COALESCE(:error, error)
            WHERE turn_id = :turn_id AND thread_id = :thread_id
              AND state IN ('requested', 'running')
            RETURNING turn_id
            """
        ),
        {
            "thread_id": thread_id,
            "turn_id": event.turn_id,
            "state": "completed" if completed else "failed" if failed else "interrupted",
            "run_id": ended_run_id,
            "completed_at": occurred_at,
            "error": event.error if failed else None,
        },
    )
    if result.scalar_one_or_none() is None:
        return
    settled = await conn.execute(
        text(
            """
            UPDATE thread SET
                status = CASE WHEN EXISTS (
                    SELECT 1 FROM thread_turn
                    WHERE thread_id = :thread_id AND state IN ('requested', 'running')
                ) THEN 'running' ELSE :settled END,
                updated_at = clock_timestamp()
            WHERE thread_id = :thread_id
            RETURNING status
            """
        ),
        {"thread_id": thread_id, "settled": "error" if failed else "idle"},
    )
    thread_status = settled.scalar_one_or_none()
    if not live or thread_status is None:
        return
    index_status: ThreadIndexStatus = (
        "running"
        if thread_status == "running"
        else "error"
        if failed
        else "finished"
        if completed
        else "interrupted"
    )
    # Another open turn's run is the thread's latest; this one must not replace it.
    await mark_thread_index_status(
        conn,
        thread_id,
        status=index_status,
        run_id=None if index_status == "running" else ended_run_id,
    )


async def _set_thread_status(conn: AsyncConnection, thread_id: str, *, status: str) -> None:
    await conn.execute(
        text(
            """
            UPDATE thread SET status = :status, updated_at = clock_timestamp()
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
                namespace, created_at
            )
            VALUES (
                :message_id, :thread_id, :turn_id, :version, 'ai', :text, :reasoning,
                :namespace, :created_at
            )
            ON CONFLICT (thread_id, message_id) DO UPDATE SET
                version = EXCLUDED.version,
                text = thread_message.text || EXCLUDED.text,
                reasoning = thread_message.reasoning || EXCLUDED.reasoning
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
                namespace, sender, attachments, usage, created_at
            )
            VALUES (
                :message_id, :thread_id, :turn_id, :version, :role, :text, :reasoning,
                :namespace, CAST(:sender AS jsonb), CAST(:attachments AS jsonb),
                CAST(:usage AS jsonb), :created_at
            )
            ON CONFLICT (thread_id, message_id) DO UPDATE SET
                version = EXCLUDED.version,
                role = EXCLUDED.role,
                text = EXCLUDED.text,
                reasoning = EXCLUDED.reasoning,
                namespace = EXCLUDED.namespace,
                sender = COALESCE(EXCLUDED.sender, thread_message.sender),
                attachments = COALESCE(EXCLUDED.attachments, thread_message.attachments),
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
            "attachments": _models_json(event.attachments),
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
    await conn.execute(
        text(
            """
            UPDATE thread_tool_call SET
                version = :version,
                status = :status,
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
            "output_preview": event.output_preview,
            "output_truncated": event.output_truncated,
            "ended_at": occurred_at,
        },
    )
