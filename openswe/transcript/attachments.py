"""Attachment bytes: written beside an event, read back by the transcript API.

An event never carries base64 — it carries an ``attachment_id``, and the bytes
live in ``thread_attachment``. The write happens in the same transaction as the
append that references it, so a client that sees the event can always fetch the
bytes, and a rolled-back append leaves no orphan row.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from openswe.attachments import MAX_ATTACHMENT_BYTES, SandboxAttachment, UnsupportedAttachment
from openswe.transcript.events import MessageAttachment
from openswe.transcript.snapshot import reading


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
        if len(self.data) > MAX_ATTACHMENT_BYTES:
            raise UnsupportedAttachment("attachment exceeds the size limit")


def capture(
    message_id: str, attachments: Sequence[SandboxAttachment]
) -> tuple[list[MessageAttachment], tuple[PendingAttachment, ...]]:
    """The metadata an event carries for ``attachments``, and the bytes to store beside it."""
    metadata: list[MessageAttachment] = []
    pending: list[PendingAttachment] = []
    for position, placed in enumerate(attachments):
        attachment_id = uuid.uuid7()
        pending.append(
            PendingAttachment(
                attachment_id=attachment_id,
                message_id=message_id,
                position=position,
                mime_type=placed.attachment.mime_type,
                file_name=placed.attachment.file_name,
                data=placed.attachment.data,
            )
        )
        metadata.append(
            MessageAttachment(
                mime_type=placed.attachment.mime_type,
                file_name=placed.attachment.file_name,
                attachment_id=attachment_id,
                path=placed.sandbox_path,
            )
        )
    return metadata, tuple(pending)


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


async def load(
    thread_id: str, attachment_id: UUID, *, conn: AsyncConnection | None = None
) -> StoredAttachment | None:
    """One attachment, addressed by the thread that owns it as well as its id."""
    async with reading(conn) as conn:
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
