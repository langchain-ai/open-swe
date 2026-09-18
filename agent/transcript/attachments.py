"""Attachment bytes: written beside an event, read back by the transcript API.

An event never carries base64 — it carries an ``attachment_id``, and the bytes
live in ``thread_attachment``. The write happens in the same transaction as the
append that references it, so a client that sees the event can always fetch the
bytes, and a rolled-back append leaves no orphan row.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.database import postgres

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
"""Matches the per-image cap the dashboard command path already enforces."""

ALLOWED_MIME_PREFIX = "image/"


class UnsupportedAttachment(ValueError):
    """The attachment is not an image, or is larger than the cap."""


@dataclass(frozen=True, kw_only=True)
class PendingAttachment:
    """Bytes to store alongside the command that references them."""

    attachment_id: UUID
    message_id: str
    position: int
    mime_type: str
    file_name: str | None
    data: bytes

    def __post_init__(self) -> None:
        if not self.mime_type.startswith(ALLOWED_MIME_PREFIX):
            raise UnsupportedAttachment(f"unsupported attachment type: {self.mime_type}")
        if len(self.data) > MAX_ATTACHMENT_BYTES:
            raise UnsupportedAttachment("attachment exceeds the 10MB limit")


@dataclass(frozen=True, kw_only=True)
class StoredAttachment:
    mime_type: str
    file_name: str | None
    data: bytes


async def write(
    conn: AsyncConnection, thread_id: str, attachments: Sequence[PendingAttachment]
) -> None:
    """Insert ``attachments`` for ``thread_id`` in the caller's transaction."""
    for attachment in attachments:
        await conn.execute(
            text(
                """
                INSERT INTO thread_attachment (
                    attachment_id, thread_id, message_id, position, mime_type, file_name, data
                )
                VALUES (
                    :attachment_id, :thread_id, :message_id, :position, :mime_type,
                    :file_name, :data
                )
                ON CONFLICT (attachment_id) DO NOTHING
                """
            ),
            {
                "attachment_id": attachment.attachment_id,
                "thread_id": thread_id,
                "message_id": attachment.message_id,
                "position": attachment.position,
                "mime_type": attachment.mime_type,
                "file_name": attachment.file_name,
                "data": attachment.data,
            },
        )


async def load(thread_id: str, attachment_id: UUID) -> StoredAttachment | None:
    """One attachment, addressed by the thread that owns it as well as its id."""
    async with postgres.snapshot_transaction() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        """
                        SELECT mime_type, file_name, data FROM thread_attachment
                        WHERE thread_id = :thread_id AND attachment_id = :attachment_id
                        """
                    ),
                    {"thread_id": thread_id, "attachment_id": attachment_id},
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        return None
    return StoredAttachment(
        mime_type=row["mime_type"],
        file_name=row["file_name"],
        data=bytes(row["data"]),
    )
