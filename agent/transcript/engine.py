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
from agent.transcript import listener, projections
from agent.transcript.events import (
    SCHEMA_VERSION,
    ActorKind,
    StoredEvent,
    TranscriptEvent,
)

logger = logging.getLogger(__name__)

NOTIFY_CHANNEL = "open_swe_thread_events"
"""LISTEN/NOTIFY channel carrying ``<thread_id>:<version>`` — ids only, never content."""


@dataclass(frozen=True, kw_only=True)
class Command:
    """One event to append, with the id that makes appending it idempotent."""

    command_id: str
    event: TranscriptEvent
    actor_kind: ActorKind
    run_id: str | None = None
    turn_id: uuid.UUID | None = None
    occurred_at: datetime | None = None


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
    not appended again.
    """
    if not commands:
        return AppendResult(thread_id=thread_id, versions=[], events=[])

    async with postgres.transaction() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:thread_id))"),
            {"thread_id": thread_id},
        )
        accepted = await _accepted_versions(conn, [command.command_id for command in commands])
        head = await _head_version(conn, thread_id)
        pending = [command for command in commands if command.command_id not in accepted]
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
                {"channel": NOTIFY_CHANNEL, "payload": f"{thread_id}:{version}"},
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


async def has_transcript(thread_id: str) -> bool:
    """Whether the thread is served by the event log rather than by LangGraph state."""
    if not postgres.configured():
        return False
    async with postgres.read_only_transaction() as conn:
        result = await conn.execute(
            text("SELECT 1 FROM thread WHERE thread_id = :thread_id"),
            {"thread_id": thread_id},
        )
        return result.scalar_one_or_none() is not None


async def _accepted_versions(conn: AsyncConnection, command_ids: Sequence[str]) -> dict[str, int]:
    result = await conn.execute(
        text(
            """
            SELECT command_id, result_version FROM thread_command_receipt
            WHERE command_id = ANY(:command_ids) AND status = 'accepted'
              AND result_version IS NOT NULL
            """
        ).bindparams(bindparam("command_ids", type_=ARRAY(Text))),
        {"command_ids": list(command_ids)},
    )
    return {row.command_id: row.result_version for row in result}


async def _head_version(conn: AsyncConnection, thread_id: str) -> int | None:
    result = await conn.execute(
        text("SELECT version FROM thread WHERE thread_id = :thread_id"),
        {"thread_id": thread_id},
    )
    return result.scalar_one_or_none()


async def _write(
    conn: AsyncConnection, thread_id: str, version: int, command: Command
) -> StoredEvent:
    event = command.event
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
    await projections.apply(
        conn,
        thread_id=thread_id,
        version=version,
        event=event,
        run_id=run_id,
        occurred_at=occurred_at,
    )
    await conn.execute(
        text(
            """
            INSERT INTO thread_command_receipt (command_id, thread_id, status, result_version)
            VALUES (:command_id, :thread_id, 'accepted', :result_version)
            ON CONFLICT (command_id) DO UPDATE SET
                status = 'accepted',
                result_version = EXCLUDED.result_version,
                error = NULL,
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
