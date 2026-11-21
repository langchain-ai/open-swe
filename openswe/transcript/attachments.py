"""Attachment bytes: written beside an event, read back by the transcript API.

An event never carries base64 — it carries an ``attachment_id``, and the bytes
live in ``thread_attachment``. The write happens in the same transaction as the
append that references it, so a client that sees the event can always fetch the
bytes, and a rolled-back append leaves no orphan row.

A thread's state still carries each image inline, so a client reading the state
gets a link to the stored copy in its place (:class:`ThreadAttachments`).
"""

import logging
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Self
from urllib.parse import quote
from uuid import UUID

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from pydantic import JsonValue
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from openswe.transcript.snapshot import reading

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
"""Matches the per-attachment cap the dashboard command path already enforces."""

LINKED_MIN_CHARS: Final = 4 * 1024
"""Inline base64 shorter than this stays in a client's state: a request for it costs more."""

_DATA_URL: Final = re.compile(r"data:([\w.+-]+/[\w.+-]+);base64,(.*)", re.DOTALL)

ALLOWED_MIME_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "application/octet-stream",
        "application/pdf",
        "text/plain",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)
"""The bytes are served back with their stored type, so the list is exact."""


class UnsupportedAttachment(ValueError):
    """The attachment is not an allowed type, or is larger than the cap."""


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
        if self.mime_type not in ALLOWED_MIME_TYPES:
            raise UnsupportedAttachment(f"unsupported attachment type: {self.mime_type}")
        if len(self.data) > MAX_ATTACHMENT_BYTES:
            raise UnsupportedAttachment("attachment exceeds the 10MB limit")


@dataclass(frozen=True, kw_only=True)
class EncodedImage:
    """The bytes a standard image block carries inline, still base64: its ``base64``, or a data URL."""

    mime_type: str
    encoded: str

    @classmethod
    def of(cls, block: Mapping[str, object]) -> Self | None:
        mime_type = block.get("mime_type")
        encoded = block.get("base64")
        url = block.get("url")
        if not isinstance(encoded, str) and isinstance(url, str):
            if match := _DATA_URL.fullmatch(url):
                mime_type, encoded = match[1], match[2]
        if not isinstance(mime_type, str) or not isinstance(encoded, str):
            return None
        return cls(mime_type=mime_type, encoded=encoded)

    @property
    def size(self) -> int | None:
        """The decoded length, worked out from the encoded one; ``None`` if that isn't base64's."""
        if len(self.encoded) % 4:
            return None
        padding = 2 if self.encoded.endswith("==") else 1 if self.encoded.endswith("=") else 0
        return len(self.encoded) // 4 * 3 - padding

    @property
    def storable(self) -> bool:
        """Whether the transcript keeps a copy: :class:`PendingAttachment` refuses the rest."""
        size = self.size
        return (
            size is not None
            and size <= MAX_ATTACHMENT_BYTES
            and self.mime_type in ALLOWED_MIME_TYPES
        )


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


@dataclass(frozen=True, kw_only=True)
class InlineImage:
    """An image a message of a thread's state carries inline, where the transcript stored its copy.

    The transcript stores a person's images under their message's id and a tool's under its
    call's, each at its position among the message's images in ``content_blocks`` order: every
    image it kept a copy of counts, and so, for a person's message, does every remote image it
    only links to.
    """

    content: list[JsonValue]
    index: int
    message_id: str
    position: int
    image: EncodedImage
    file_name: str | None

    @classmethod
    def all_in(cls, messages: Iterable[JsonValue]) -> list[Self]:
        """Those of :data:`LINKED_MIN_CHARS` or more in ``messages``."""
        return [
            image for message in messages if isinstance(message, dict) for image in cls._in(message)
        ]

    @classmethod
    def _in(cls, message: dict[str, JsonValue]) -> Iterator[Self]:
        content = message.get("content")
        if not isinstance(content, list):
            return
        items = [item for item in content if isinstance(item, str | dict)]
        if len(items) < len(content) or not any(
            isinstance(item, dict) and item.get("type") in ("image", "image_url") for item in items
        ):
            return
        parsed: BaseMessage
        try:
            match message.get("type"), message.get("id"), message.get("tool_call_id"):
                case "human", str(message_id), _:
                    parsed, keep_remote = HumanMessage(content=items), True
                case "tool", _, str(message_id):
                    parsed = ToolMessage(content=items, tool_call_id=message_id)
                    keep_remote = False
                case _:
                    return
            blocks = parsed.content_blocks
        except Exception:
            logger.debug("Could not read content blocks for state images", exc_info=True)
            return
        position = 0
        for index, block in enumerate(blocks):
            if block.get("type") != "image":
                continue
            image = EncodedImage.of(block)
            if image is None:
                url = block.get("url")
                if keep_remote and isinstance(url, str) and url and not url.startswith("data:"):
                    position += 1
                continue
            if not image.storable:
                continue
            if len(image.encoded) >= LINKED_MIN_CHARS:
                file_name = block.get("file_name")
                yield cls(
                    content=content,
                    index=index,
                    message_id=message_id,
                    position=position,
                    image=image,
                    file_name=file_name if isinstance(file_name, str) else None,
                )
            position += 1

    def link(self, url: str) -> None:
        """Put ``url`` in place of the bytes, keeping the image's type, mime type and file name."""
        block: dict[str, JsonValue] = {
            "type": "image",
            "url": url,
            "mime_type": self.image.mime_type,
        }
        if self.file_name:
            block["file_name"] = self.file_name
        self.content[self.index] = block


@dataclass(frozen=True, kw_only=True)
class ListedAttachment:
    """A stored attachment without its bytes."""

    attachment_id: UUID
    mime_type: str
    size: int


class ThreadAttachments:
    """A thread's stored attachments without their bytes, by the message and position of each."""

    def __init__(
        self, thread_id: str, listed: Mapping[tuple[str, int], Sequence[ListedAttachment]]
    ) -> None:
        self.thread_id = thread_id
        self.listed = listed

    @classmethod
    async def of(cls, thread_id: str, *, conn: AsyncConnection | None = None) -> Self:
        """One read of the thread's rows, where ``octet_length`` takes each length from the
        stored value's header rather than its bytes."""
        async with reading(conn) as conn:
            rows = (
                (
                    await conn.execute(
                        text(
                            """
                            SELECT attachment_id, message_id, position, mime_type,
                                octet_length(data) AS size
                            FROM thread_attachment WHERE thread_id = :thread_id
                            """
                        ),
                        {"thread_id": thread_id},
                    )
                )
                .mappings()
                .all()
            )
        listed: defaultdict[tuple[str, int], list[ListedAttachment]] = defaultdict(list)
        for row in rows:
            listed[row["message_id"], row["position"]].append(
                ListedAttachment(
                    attachment_id=row["attachment_id"], mime_type=row["mime_type"], size=row["size"]
                )
            )
        return cls(thread_id, listed)

    def link(self, images: Iterable[InlineImage]) -> None:
        """Link each image to the copy stored for its message and position, when that copy is
        the same type and length; any other keeps its bytes."""
        for image in images:
            stored = next(
                (
                    candidate
                    for candidate in self.listed.get((image.message_id, image.position), ())
                    if candidate.mime_type == image.image.mime_type
                    and candidate.size == image.image.size
                ),
                None,
            )
            if stored is not None:
                image.link(self.url(stored.attachment_id))

    def url(self, attachment_id: UUID) -> str:
        """Where the dashboard serves the attachment, after the same read check as the thread's
        (``openswe.transcript.routes.api_get_thread_attachment``)."""
        thread_id = quote(self.thread_id, safe="")
        return f"/dashboard/api/threads/{thread_id}/transcript/attachments/{attachment_id}"
