"""Appending to the transcript log: one transaction, gapless versions, receipts.

``append`` is the only writer. It takes an advisory lock on the thread, reads
the thread's head version, and assigns ``version + 1 ...`` to the commands it
actually appends, so the log is gapless per thread and a reader can resume from
any version with ``version > after``. Events, projections, receipts, the new
head and the notification all commit together — a subscriber that reacts to the
notification can never read a version that is not there yet.

Idempotency is the command receipt: replaying a command that was already
accepted returns the version it produced instead of appending a second event.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ARRAY, Text, bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.database import postgres
from agent.transcript import attachments as attachment_store
from agent.transcript import listener, projections
from agent.transcript.events import (
    SCHEMA_VERSION,
    ActorKind,
    StoredEvent,
    TranscriptEvent,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class Command:
    """One event to append, with the id that makes appending it idempotent.

    ``attachments`` are the bytes the event refers to by ``attachment_id``.
    They are written by the same transaction as the event, and only when the
    event is actually appended — a replayed command writes neither again.
    ``tool_output`` travels the same way: a ``tool.completed`` event carries
    only a preview, so the full output reaches the projection here instead of
    on the wire.
    """

    command_id: str
    event: TranscriptEvent
    actor_kind: ActorKind
    run_id: str | None = None
    turn_id: uuid.UUID | None = None
    occurred_at: datetime | None = None
    attachments: tuple[attachment_store.PendingAttachment, ...] = ()
    tool_output: str | None = None


@dataclass(frozen=True, kw_only=True)
class AppendResult:
    thread_id: str
    versions: list[int]
    events: list[StoredEvent]


class ThreadNotTranscribed(Exception):
    """The thread has no transcript, and the first command would not create one."""

    def __init__(self, thread_id: str) -> None:
        super().__init__(f"thread {thread_id} has no transcript")
        self.thread_id = thread_id


async def append(thread_id: str, commands: Sequence[Command]) -> AppendResult:
    """Append ``commands`` to ``thread_id``'s log and project them, in one transaction.

    Returns one version per command in order; a command whose receipt says it
    was already accepted reports the version it produced the first time and is
    not appended again. A ``command_id`` repeated within one batch is treated
    the same way: only its first occurrence is appended, and the later copies
    report that occurrence's version.
    """
    if not commands:
        return AppendResult(thread_id=thread_id, versions=[], events=[])

    async with postgres.transaction() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:thread_id))"),
            {"thread_id": thread_id},
        )
        accepted = await _accepted_versions(
            conn, thread_id, [command.command_id for command in commands]
        )
        head = (
            await conn.execute(
                text("SELECT version FROM thread WHERE thread_id = :thread_id"),
                {"thread_id": thread_id},
            )
        ).scalar_one_or_none()
        seen: set[str] = set(accepted)
        pending: list[Command] = []
        for command in commands:
            if command.command_id in seen:
                continue
            seen.add(command.command_id)
            pending.append(command)
        if head is None and pending and pending[0].event.type != "thread.created":
            raise ThreadNotTranscribed(thread_id)

        version = head or 0
        events: list[StoredEvent] = []
        for command in pending:
            version += 1
            events.append(await _write(conn, thread_id, version, command))
        if events:
            await conn.execute(
                text(
                    """
                    UPDATE thread SET version = :version, updated_at = clock_timestamp()
                    WHERE thread_id = :thread_id
                    """
                ),
                {"thread_id": thread_id, "version": version},
            )
            await conn.execute(
                text("SELECT pg_notify(:channel, :payload)"),
                {"channel": listener.CHANNEL, "payload": f"{thread_id}:{version}"},
            )

    appended = {event.command_id: event.version for event in events}
    versions: list[int] = []
    for command in commands:
        stored = accepted.get(command.command_id)
        versions.append(stored if stored is not None else appended[command.command_id])
    if events:
        # The graph and the HTTP app share a process, so a subscriber here does
        # not have to wait for the notification to come back from Postgres.
        listener.publish(thread_id, version)
        logger.info(
            "Appended transcript events",
            extra={
                "transcript": {
                    "thread_id": thread_id,
                    "version": version,
                    "appended": len(events),
                    "event_types": [event.event_type for event in events],
                }
            },
        )
    return AppendResult(thread_id=thread_id, versions=versions, events=events)


async def delete_transcript(thread_id: str) -> bool:
    """Drop the thread's transcript, and tell its subscribers the thread is gone.

    Everything else — events, turns, messages, tool calls, attachments —
    cascades from the ``thread`` row. The receipts do not — they carry no
    foreign key — so they are deleted explicitly here: leaving them behind
    would deduplicate a thread recreated under the same id out of ever being
    created. Returns whether a transcript was deleted.
    """
    if not postgres.configured():
        return False
    async with postgres.transaction() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:thread_id))"),
            {"thread_id": thread_id},
        )
        result = await conn.execute(
            text("DELETE FROM thread WHERE thread_id = :thread_id RETURNING thread_id"),
            {"thread_id": thread_id},
        )
        deleted = result.scalar_one_or_none() is not None
        await conn.execute(
            text("DELETE FROM thread_command_receipt WHERE thread_id = :thread_id"),
            {"thread_id": thread_id},
        )
        if deleted:
            await conn.execute(
                text("SELECT pg_notify(:channel, :payload)"),
                {"channel": listener.CHANNEL, "payload": f"{thread_id}:{listener.DELETED}"},
            )
    if deleted:
        listener.publish(thread_id, listener.DELETED_VERSION)
        logger.info(
            "Deleted a thread transcript",
            extra={"transcript": {"thread_id": thread_id}},
        )
    return deleted


async def _accepted_versions(
    conn: AsyncConnection, thread_id: str, command_ids: Sequence[str]
) -> dict[str, int]:
    result = await conn.execute(
        text(
            """
            SELECT command_id, result_version FROM thread_command_receipt
            WHERE thread_id = :thread_id AND command_id = ANY(:command_ids)
              AND result_version IS NOT NULL
            """
        ).bindparams(bindparam("command_ids", type_=ARRAY(Text))),
        {"thread_id": thread_id, "command_ids": list(command_ids)},
    )
    return {row.command_id: row.result_version for row in result}


async def _write(
    conn: AsyncConnection, thread_id: str, version: int, command: Command
) -> StoredEvent:
    event = command.event
    # Identity a writer could only guess at outside the thread's lock is
    # settled here, so the log, the receipt and the projection all agree.
    event = await projections.resolve(conn, thread_id, event)
    payload = event.model_dump(mode="json")
    run_id = command.run_id or _payload_run_id(payload)
    turn_id = command.turn_id or _payload_turn_id(payload)
    event_id = uuid.uuid7()
    await projections.ensure_thread_row(conn, thread_id, event)
    result = await conn.execute(
        text(
            """
            INSERT INTO thread_event (
                thread_id, version, event_id, event_type, schema_version, run_id, turn_id,
                command_id, actor_kind, occurred_at, payload
            )
            VALUES (
                :thread_id, :version, :event_id, :event_type, :schema_version, :run_id, :turn_id,
                :command_id, :actor_kind, COALESCE(:occurred_at, clock_timestamp()),
                CAST(:payload AS jsonb)
            )
            RETURNING occurred_at
            """
        ),
        {
            "thread_id": thread_id,
            "version": version,
            "event_id": event_id,
            "event_type": event.type,
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "turn_id": turn_id,
            "command_id": command.command_id,
            "actor_kind": command.actor_kind,
            "occurred_at": command.occurred_at,
            "payload": event.model_dump_json(),
        },
    )
    occurred_at = result.scalar_one()
    await attachment_store.write(conn, thread_id, command.attachments)
    await projections.apply(
        conn,
        thread_id=thread_id,
        version=version,
        event=event,
        run_id=run_id,
        occurred_at=occurred_at,
        tool_output=command.tool_output,
    )
    await conn.execute(
        text(
            """
            INSERT INTO thread_command_receipt (thread_id, command_id, result_version)
            VALUES (:thread_id, :command_id, :result_version)
            ON CONFLICT (thread_id, command_id) DO UPDATE SET
                result_version = EXCLUDED.result_version,
                accepted_at = clock_timestamp()
            """
        ),
        {"command_id": command.command_id, "thread_id": thread_id, "result_version": version},
    )
    return StoredEvent(
        thread_id=thread_id,
        version=version,
        event_id=event_id,
        event_type=event.type,
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        turn_id=turn_id,
        command_id=command.command_id,
        actor_kind=command.actor_kind,
        occurred_at=occurred_at,
        payload=payload,
    )


def _payload_run_id(payload: dict[str, object]) -> str | None:
    value = payload.get("run_id")
    return value if isinstance(value, str) and value else None


def _payload_turn_id(payload: dict[str, object]) -> uuid.UUID | None:
    value = payload.get("turn_id")
    return uuid.UUID(value) if isinstance(value, str) and value else None
