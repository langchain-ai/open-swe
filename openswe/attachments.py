"""Files a person attaches to a message.

A file is written into the thread's sandbox when its message arrives, and the
message names it there: each file is an ``attachment_N`` element of the message's
envelope giving its sandbox ``path``. The model opens it with ``read_file`` when it
wants to; it never receives the bytes.
"""

import base64
import binascii
import logging
import mimetypes
import posixpath
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from openswe.input_messages import FieldValue, input_message_elements

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_ATTACHMENTS_PER_MESSAGE = 10
SANDBOX_ATTACHMENTS_DIR = "/tmp/open-swe/attachments"
ATTACHMENT_ELEMENT_PREFIX = "attachment_"
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
        """The file a base64 ``image``/``file`` content block (or image data URL) carries."""
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


@dataclass(frozen=True, kw_only=True)
class StagedAttachment:
    """An attachment written into the sandbox, or the reason it could not be."""

    attachment: Attachment
    path: str
    error: str | None = None

    def fields(self) -> dict[str, FieldValue | None]:
        return {
            "path": self.path,
            "mime_type": self.attachment.mime_type,
            "name": self.attachment.file_name,
            "error": self.error,
        }


def envelope_data(
    attachments: Sequence[Mapping[str, FieldValue | None]],
) -> dict[str, Mapping[str, FieldValue | None]]:
    """The ``attachment_N`` elements an input message's envelope carries, from each one's fields."""
    return {
        f"{ATTACHMENT_ELEMENT_PREFIX}{index}": fields
        for index, fields in enumerate(attachments, start=1)
    }


def envelope_attachments(content: object) -> list[dict[str, str]]:
    """The ``path``/``mime_type``/``name`` fields of each attachment a message's envelope names."""
    return [
        fields
        for fields in input_message_elements(content, ATTACHMENT_ELEMENT_PREFIX)
        if fields.get("path")
    ]


async def stage(
    thread_id: str, attachments: Sequence[Attachment], *, workspace_slug: str | None
) -> list[StagedAttachment]:
    """Write ``attachments`` into the thread's sandbox, in order, under a fresh directory."""
    if not attachments:
        return []
    directory = posixpath.join(SANDBOX_ATTACHMENTS_DIR, str(uuid.uuid7()))
    used: set[str] = set()
    paths = [
        posixpath.join(directory, _unique(_file_name(attachment, index), used))
        for index, attachment in enumerate(attachments)
    ]
    try:
        from openswe.sandboxes.lifecycle import ensure_sandbox_for_thread

        backend = await ensure_sandbox_for_thread(thread_id, workspace_slug=workspace_slug)
        responses = await backend.aupload_files(
            [(path, attachment.data) for path, attachment in zip(paths, attachments, strict=True)]
        )
        errors = [str(response.error) if response.error else None for response in responses]
    except Exception:
        logger.warning(
            "Could not write attachments into the sandbox",
            exc_info=True,
            extra={"attachment_thread_id": thread_id},
        )
        errors = ["the sandbox was unavailable"] * len(attachments)
    for path, error in zip(paths, errors, strict=True):
        if error:
            logger.warning(
                "Could not write an attachment into the sandbox",
                extra={"attachment_path": path, "attachment_error": error},
            )
    return [
        StagedAttachment(attachment=attachment, path=path, error=error)
        for attachment, path, error in zip(attachments, paths, errors, strict=True)
    ]


def _decode(encoded: str) -> bytes:
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise UnsupportedAttachment("invalid attachment data") from exc


def _file_name(attachment: Attachment, index: int) -> str:
    if attachment.file_name:
        name = _UNSAFE_NAME.sub("_", posixpath.basename(attachment.file_name)).strip("._")[:120]
        if name:
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
