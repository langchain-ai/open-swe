"""Streamed fragments of AI messages, as the transcript appends them.

The transcript middleware appends the fragments of the model calls it sees
itself. A run hosted on another runtime calls its model there, so it posts its
fragments back instead (``openswe_transcript.remote``), and ``append_fragments``
appends them to that run's turn. The run token names the thread and the run's
own turn, the one its hooks started here, is the only one it may write to, and
only while that turn is open.
"""

from typing import Final
from uuid import UUID

from openswe_transcript.wire import Fragment, FragmentBatch
from sqlalchemy import ARRAY, Text, bindparam, text

from openswe.database import postgres
from openswe.transcript.engine import Command, append
from openswe.transcript.events import MessageAppended
from openswe.transcript.turns import run_turn

_FOREIGN_MESSAGES: Final = text(
    """
    SELECT message_id FROM thread_message
    WHERE thread_id = :thread_id AND message_id = ANY(:message_ids)
      AND (role <> 'ai' OR turn_id <> :turn_id)
    """
).bindparams(bindparam("message_ids", type_=ARRAY(Text)))
_ACCEPTED_COMMANDS: Final = text(
    """
    SELECT command_id FROM thread_command_receipt
    WHERE thread_id = :thread_id AND command_id = ANY(:command_ids)
      AND result_version IS NOT NULL
    """
).bindparams(bindparam("command_ids", type_=ARRAY(Text)))


class FragmentsRefused(Exception):
    """The fragments cannot join the run's transcript; the message says why."""


def message_completed_command_id(message_id: str) -> str:
    """The command id of an AI message's ``message.completed``."""
    return f"message:{message_id}:completed"


def fragment_command(fragment: Fragment, *, turn_id: UUID, run_id: str) -> Command:
    return Command(
        command_id=f"fragment:{fragment.fragment_id}",
        event=MessageAppended(
            turn_id=turn_id,
            message_id=fragment.message_id,
            namespace=list(fragment.namespace),
            text=fragment.text,
            reasoning=fragment.reasoning,
        ),
        actor_kind="agent",
        run_id=run_id,
        turn_id=turn_id,
    )


async def append_fragments(thread_id: str, batch: FragmentBatch) -> int:
    """Append a remote run's fragments to its open turn; returns how many were new.

    Fragments of a message that already completed are dropped, since the
    completed text is canonical. Raises ``FragmentsRefused`` when the run has no
    open turn, or when a fragment names a message that is not an AI message of
    that turn, such as something a person said.
    """
    if not postgres.configured():
        raise FragmentsRefused("this backend keeps no transcripts")
    turn = await run_turn(thread_id, batch.run_id)
    if turn is None or not turn.open:
        raise FragmentsRefused("the run has no open turn")
    message_ids = sorted({fragment.message_id for fragment in batch.fragments})
    async with postgres.read_only_transaction() as conn:
        foreign = (
            await conn.execute(
                _FOREIGN_MESSAGES,
                {"thread_id": thread_id, "message_ids": message_ids, "turn_id": turn.turn_id},
            )
        ).first()
        completed = set(
            (
                await conn.execute(
                    _ACCEPTED_COMMANDS,
                    {
                        "thread_id": thread_id,
                        "command_ids": [message_completed_command_id(m) for m in message_ids],
                    },
                )
            ).scalars()
        )
    if foreign is not None:
        raise FragmentsRefused("a fragment names a message outside the run's turn")
    commands = [
        fragment_command(fragment, turn_id=turn.turn_id, run_id=batch.run_id)
        for fragment in batch.fragments
        if message_completed_command_id(fragment.message_id) not in completed
    ]
    return len((await append(thread_id, commands)).events)
