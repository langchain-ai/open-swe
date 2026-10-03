"""Follow-ups held for a running agent's next model call.

Each message is its own ``thread_queued_message`` row: writers only insert and
the run only deletes the rows it consumed, so no update can overwrite another.
"""

import json
import logging
from collections.abc import Sequence

from pydantic import BaseModel
from sqlalchemy import bindparam, text

from agent.database import postgres
from agent.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

MAX_QUEUED_MESSAGES = 100

type QueuedContent = str | list[JsonObject] | JsonObject


class QueuedMessage(BaseModel):
    seq: int
    content: QueuedContent


class MessageQueue:
    """The follow-ups waiting for one thread's run, oldest first."""

    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id

    async def put(self, content: QueuedContent, *, queue_id: str | None = None) -> None:
        """Queue ``content``; a ``queue_id`` already queued is not queued again."""
        async with postgres.transaction() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO thread_queued_message (thread_id, queue_id, content)
                    VALUES (:thread_id, :queue_id, CAST(:content AS jsonb))
                    ON CONFLICT (thread_id, queue_id) WHERE queue_id IS NOT NULL DO NOTHING
                    """
                ),
                {"thread_id": self.thread_id, "queue_id": queue_id, "content": json.dumps(content)},
            )
            dropped = await conn.execute(
                text(
                    """
                    DELETE FROM thread_queued_message
                    WHERE thread_id = :thread_id AND seq < (
                        SELECT seq FROM thread_queued_message
                        WHERE thread_id = :thread_id
                        ORDER BY seq DESC
                        OFFSET :newest_kept LIMIT 1
                    )
                    """
                ),
                {"thread_id": self.thread_id, "newest_kept": MAX_QUEUED_MESSAGES - 1},
            )
        if dropped.rowcount:
            logger.warning(
                "Dropped the oldest queued messages over the cap",
                extra={"message_queue": {"thread_id": self.thread_id, "dropped": dropped.rowcount}},
            )

    async def messages(self) -> list[QueuedMessage]:
        async with postgres.read_only_transaction() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT seq, content FROM thread_queued_message
                    WHERE thread_id = :thread_id
                    ORDER BY seq
                    """
                ),
                {"thread_id": self.thread_id},
            )
            rows = result.mappings().all()
        return [QueuedMessage.model_validate(dict(row)) for row in rows]

    async def remove(self, messages: Sequence[QueuedMessage]) -> None:
        if not messages:
            return
        async with postgres.transaction() as conn:
            await conn.execute(
                text("DELETE FROM thread_queued_message WHERE seq IN :seqs").bindparams(
                    bindparam("seqs", expanding=True)
                ),
                {"seqs": [message.seq for message in messages]},
            )

    async def clear(self) -> None:
        async with postgres.transaction() as conn:
            await conn.execute(
                text("DELETE FROM thread_queued_message WHERE thread_id = :thread_id"),
                {"thread_id": self.thread_id},
            )
