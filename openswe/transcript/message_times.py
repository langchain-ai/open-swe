"""When each of a thread's messages was sent, as the transcript recorded it.

A thread's state carries no per-message time, so a client reading the state gets
the transcript's in each message's ``response_metadata.created_at``. A
subagent's messages are read from its own checkpoints or event stream instead,
so a client asks for its times (``SubagentTimes``) separately.
"""

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Self

from pydantic import BaseModel, JsonValue
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from openswe.transcript.snapshot import reading


class MessageTimes:
    """A thread's recorded message times, by message id."""

    def __init__(self, recorded: Mapping[str, datetime]) -> None:
        self.recorded = recorded

    @classmethod
    async def of(
        cls, thread_id: str, message_ids: Iterable[str], *, conn: AsyncConnection | None = None
    ) -> Self:
        """One read of the times recorded for ``message_ids``."""
        ids = list(message_ids)
        if not ids:
            return cls({})
        async with reading(conn) as conn:
            rows = await conn.execute(
                text(
                    """
                    SELECT message_id, created_at FROM thread_message
                    WHERE thread_id = :thread_id AND message_id = ANY(:message_ids)
                    """
                ),
                {"thread_id": thread_id, "message_ids": ids},
            )
            return cls({row.message_id: row.created_at for row in rows})

    def stamp(self, messages: Iterable[dict[str, JsonValue]]) -> None:
        """Set each recorded message's ``response_metadata.created_at``, unless it already
        holds one as text: a provider's epoch number is not one a client reads."""
        for message in messages:
            message_id = message.get("id")
            if not isinstance(message_id, str) or message_id not in self.recorded:
                continue
            metadata = message.get("response_metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                message["response_metadata"] = metadata
            if not isinstance(metadata.get("created_at"), str):
                metadata["created_at"] = self.recorded[message_id].isoformat()


class SubagentTimes(BaseModel):
    """When a subagent was given its task, and when each of its replies was recorded."""

    prompt: datetime | None
    messages: dict[str, datetime]

    @classmethod
    async def of(cls, thread_id: str, tool_call_id: str, *, conn: AsyncConnection) -> Self:
        """The times of the subagent the ``task`` call ``tool_call_id`` started.

        The transcript never records the task prompt, which the model wrote, so
        it takes the time the call started; everything the subagent said is
        recorded under a namespace ending in the call's id.
        """
        prompt = await conn.scalar(
            text(
                """
                SELECT started_at FROM thread_tool_call
                WHERE thread_id = :thread_id AND tool_call_id = :tool_call_id
                """
            ),
            {"thread_id": thread_id, "tool_call_id": tool_call_id},
        )
        rows = await conn.execute(
            text(
                """
                SELECT message_id, created_at FROM thread_message
                WHERE thread_id = :thread_id
                  AND namespace[cardinality(namespace)] = :tool_call_id
                """
            ),
            {"thread_id": thread_id, "tool_call_id": tool_call_id},
        )
        return cls(prompt=prompt, messages={row.message_id: row.created_at for row in rows})
