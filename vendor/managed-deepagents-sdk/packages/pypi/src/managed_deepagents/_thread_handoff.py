"""Hand a run's thread to the conversation its post started."""

from __future__ import annotations

from typing import TYPE_CHECKING

from managed_deepagents._schedule_auth import hand_off_scheduled_thread
from managed_deepagents._thread_record import (
    THREAD_CONVERSATION_METADATA,
    ThreadRecord,
    origin_from_binding,
    thread_record_metadata,
)

if TYPE_CHECKING:
    from managed_deepagents._channels.binding import ChannelBinding
    from managed_deepagents._managed_thread import ManagedThread
    from managed_deepagents._schedule_auth import ExecutionUser


async def hand_off_thread(
    thread: ManagedThread, execution: ExecutionUser, conversation: ChannelBinding
) -> None:
    """Make a run's thread the conversation its post started, keeping its participants.

    ``execution`` is the principal the run runs as, which owns the thread: a
    scheduled run's (``schedule_execution``) or a channel run's
    (``channel_run_execution``).
    """
    record = thread_record_metadata(
        ThreadRecord(
            origin=origin_from_binding(conversation, None),
            participants={person.id: person.role for person in thread.participants},
        )
    )
    await hand_off_scheduled_thread(
        {
            "thread": thread.id,
            "execution": execution,
            "conversation": {
                "provider": conversation["provider"],
                "source_thread_key": conversation["source_thread_key"],
            },
        },
        {**record, THREAD_CONVERSATION_METADATA: conversation["source_thread_key"]},
    )
