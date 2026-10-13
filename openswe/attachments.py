"""Files a person attaches to a message.

An attachment travels as a base64 ``image`` or ``file`` content block on the
human message, so the transcript can store its bytes. The model never receives
those bytes: before each model call the file is written into the sandbox and
the block is replaced by a reference to its path, which the agent opens with
``read_file`` when it wants to.
"""

import base64
import binascii
import logging
import mimetypes
import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape

from langchain_core.messages import BaseMessage

from openswe.prompts import prompt

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_ATTACHMENTS_PER_MESSAGE = 10
SANDBOX_ATTACHMENTS_DIR = "/tmp/open-swe/attachments"
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


class UnsupportedAttachment(ValueError):
    """The attachment is unreadable or larger than the cap."""


@dataclass(frozen=True, kw_only=True)
class Attachment:
    """The bytes of one attached file, and what it is called."""

    mime_type: str
    file_name: str | None
    data: bytes

    def __post_init__(self) -> None:
        if len(self.data) > MAX_ATTACHMENT_BYTES:
            raise UnsupportedAttachment(
                f"attachment exceeds the {MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB limit"
            )

    @classmethod
    def of(cls, data: bytes, *, mime_type: str | None, file_name: str | None) -> Attachment:
        guessed = mimetypes.guess_type(file_name)[0] if file_name else None
        return cls(
            mime_type=(mime_type or "").split(";")[0].strip()
            or guessed
            or "application/octet-stream",
            file_name=file_name or None,
            data=data,
        )

    @classmethod
    def from_block(cls, block: object) -> Attachment | None:
        """The attachment a base64 ``image``/``file`` block (or image data URL) carries."""
        if not isinstance(block, Mapping):
            return None
        kind = block.get("type")
        if kind == "image_url":
            image_url = block.get("image_url")
            url = image_url.get("url") if isinstance(image_url, Mapping) else image_url
            if not isinstance(url, str):
                raise UnsupportedAttachment("invalid image data")
            header, separator, encoded = url.partition(",")
            if not separator or not header.startswith("data:") or not header.endswith(";base64"):
                raise UnsupportedAttachment("images must be embedded as base64 data URLs")
            return cls.of(_decode(encoded), mime_type=header[5:-7], file_name=None)
        if kind not in ("image", "file"):
            return None
        encoded = block.get("base64")
        if not isinstance(encoded, str):
            return None
        mime_type = block.get("mime_type") or block.get("mimeType")
        file_name = block.get("file_name") or block.get("fileName")
        return cls.of(
            _decode(encoded),
            mime_type=mime_type if isinstance(mime_type, str) else None,
            file_name=file_name if isinstance(file_name, str) else None,
        )

    def to_block(self) -> dict[str, object]:
        return {
            "type": "image" if self.mime_type.startswith("image/") else "file",
            "base64": base64.b64encode(self.data).decode("ascii"),
            "mime_type": self.mime_type,
            **({"file_name": self.file_name} if self.file_name else {}),
        }


@dataclass(frozen=True, kw_only=True)
class SandboxAttachment:
    """An attachment as it sits on a message: its place there and its sandbox path."""

    attachment: Attachment
    position: int
    sandbox_path: str

    def reference(self, *, error: str | None = None) -> dict[str, str]:
        """The text block the model sees in place of the file's bytes."""
        return {
            "type": "text",
            "text": prompt(
                "runs/message-attachment",
                path=escape(self.sandbox_path),
                mime_type=escape(self.attachment.mime_type),
                file_name=escape(self.attachment.file_name or ""),
                error=escape(error or ""),
            ),
        }


def message_attachments(
    message: BaseMessage, *, message_id: str | None = None
) -> list[SandboxAttachment]:
    """Every attachment on ``message``, keyed by content-block index, with its sandbox path."""
    message_id = message.id or message_id
    if not isinstance(message.content, list) or not message_id:
        return []
    directory = posixpath.join(SANDBOX_ATTACHMENTS_DIR, _safe_name(message_id) or "message")
    used: set[str] = set()
    found: list[SandboxAttachment] = []
    for position, block in enumerate(message.content):
        try:
            attachment = Attachment.from_block(block)
        except UnsupportedAttachment:
            logger.warning(
                "Skipping an unreadable message attachment",
                exc_info=True,
                extra={"attachment_message_id": message.id},
            )
            continue
        if attachment is None:
            continue
        name = _unique(_file_name(attachment, len(found)), used)
        found.append(
            SandboxAttachment(
                attachment=attachment,
                position=position,
                sandbox_path=posixpath.join(directory, name),
            )
        )
    return found


def _decode(encoded: str) -> bytes:
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise UnsupportedAttachment("invalid attachment data") from exc


def _safe_name(name: str) -> str:
    return _UNSAFE_NAME.sub("_", name).strip("._")[:120]


def _file_name(attachment: Attachment, index: int) -> str:
    if attachment.file_name and (name := _safe_name(posixpath.basename(attachment.file_name))):
        return name
    extension = mimetypes.guess_extension(attachment.mime_type) or ""
    return f"attachment-{index + 1}{extension}"


def _unique(name: str, used: set[str]) -> str:
    stem, dot, extension = name.partition(".")
    candidate, suffix = name, 1
    while candidate in used:
        candidate = f"{stem}-{suffix}{dot}{extension}"
        suffix += 1
    used.add(candidate)
    return candidate
