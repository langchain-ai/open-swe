"""Media attached to a thread lives in its sandbox, never in its messages.

Bytes are uploaded once at ingestion and referenced by content hash from the
``<input-message>`` envelope's ``media`` data field. Checkpoints carry the
reference, and the hydration middleware re-attaches the bytes right before a
provider call. Every entry point (dashboard, Slack, Linear, queued follow-ups)
goes through :func:`attach_thread_media`.
"""

import hashlib
import logging
import posixpath
import re
from collections import OrderedDict
from collections.abc import Iterable
from typing import Any

from deepagents.backends.protocol import SandboxBackendProtocol
from pydantic import BaseModel, ConfigDict, ValidationError

from agent.input_messages import input_message_data_items
from agent.sandboxes.lifecycle import ensure_sandbox_for_thread
from agent.sandboxes.state import get_sandbox_backend

logger = logging.getLogger(__name__)

MEDIA_DATA_KEY = "media"
MEDIA_DIR = "/uploads"
MAX_MEDIA_BYTES = 10 * 1024 * 1024
IMAGE_EXTENSIONS: dict[str, str] = {
    "image/gif": "gif",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
_MEDIA_FILE_NAME = re.compile(r"^[0-9a-f]{64}(?:-[A-Za-z0-9._-]{1,64})?\.(gif|jpg|png|webp)$")
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_MIME_BY_EXTENSION = {ext: mime for mime, ext in IMAGE_EXTENSIONS.items()}
_CACHE_LIMIT_BYTES = 256 * 1024 * 1024


class MediaError(RuntimeError):
    """The sandbox could not store or return an attachment."""


class MediaUpload(BaseModel):
    """Bytes that have been fetched and validated but not yet stored."""

    data: bytes
    mime_type: str
    file_name: str | None = None
    source_url: str | None = None


class MediaRef(BaseModel):
    """A stored attachment, as carried in the message envelope."""

    model_config = ConfigDict(extra="ignore")

    path: str
    mime_type: str
    sha256: str
    size: int
    file_name: str | None = None
    source_url: str | None = None

    @property
    def name(self) -> str:
        return posixpath.basename(self.path)

    def dump(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


def media_data(refs: Iterable[MediaRef]) -> dict[str, object]:
    """The envelope data field carrying ``refs``, empty when there are none."""
    dumped = [ref.dump() for ref in refs]
    return {MEDIA_DATA_KEY: dumped} if dumped else {}


def media_file_name(sha256: str, mime_type: str, file_name: str | None = None) -> str:
    """``<sha>-<original stem>.<ext>``: content-addressed, but readable for the model."""
    stem = posixpath.splitext(posixpath.basename(file_name or ""))[0]
    stem = _UNSAFE_NAME_CHARS.sub("-", stem).strip("-.")[:64]
    suffix = f"-{stem}" if stem else ""
    return f"{sha256}{suffix}.{IMAGE_EXTENSIONS[mime_type]}"


def media_mime_type(file_name: str) -> str | None:
    """The mime type a stored media file name encodes, or None when it is not one."""
    match = _MEDIA_FILE_NAME.match(file_name)
    return _MIME_BY_EXTENSION[match.group(1)] if match else None


async def store_media(
    backend: SandboxBackendProtocol, uploads: list[MediaUpload]
) -> list[MediaRef]:
    if not uploads:
        return []
    refs: list[MediaRef] = []
    files: dict[str, bytes] = {}
    for upload in uploads:
        digest = hashlib.sha256(upload.data).hexdigest()
        path = posixpath.join(
            MEDIA_DIR, media_file_name(digest, upload.mime_type, upload.file_name)
        )
        files[path] = upload.data
        refs.append(
            MediaRef(
                path=path,
                mime_type=upload.mime_type,
                sha256=digest,
                size=len(upload.data),
                file_name=upload.file_name,
                source_url=upload.source_url,
            )
        )
    responses = await backend.aupload_files(list(files.items()))
    failed = [response for response in responses if response.error]
    if failed:
        logger.warning(
            "Failed to store thread media",
            extra={"media_paths": [response.path for response in failed]},
        )
        raise MediaError(f"failed to store attachment: {failed[0].error}")
    for path, data in files.items():
        _cache_put(_cache_key(backend, path), data)
    return refs


async def attach_thread_media(
    thread_id: str,
    uploads: list[MediaUpload],
    *,
    environment_slug: str | None = None,
) -> list[MediaRef]:
    """Store ``uploads`` in the thread's sandbox, creating the sandbox when the thread is new."""
    if not uploads:
        return []
    backend = await ensure_sandbox_for_thread(thread_id, environment_slug=environment_slug)
    return await store_media(backend, uploads)


async def read_thread_media(thread_id: str, file_name: str) -> tuple[bytes, str] | None:
    """The bytes and mime type of one stored attachment, or None when it is gone."""
    mime_type = media_mime_type(file_name)
    if mime_type is None:
        return None
    backend = await get_sandbox_backend(thread_id)
    path = posixpath.join(MEDIA_DIR, file_name)
    data = await download_media(backend, path)
    return (data, mime_type) if data is not None else None


async def download_media(backend: SandboxBackendProtocol, path: str) -> bytes | None:
    key = _cache_key(backend, path)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    responses = await backend.adownload_files([path])
    response = responses[0] if responses else None
    if response is None or response.error or response.content is None:
        logger.warning(
            "Thread media is missing from the sandbox",
            extra={"media_path": path, "media_error": getattr(response, "error", None)},
        )
        return None
    _cache_put(key, response.content)
    return response.content


def media_refs_from_content(content: object) -> list[MediaRef]:
    """The attachments an input-message envelope references."""
    refs: list[MediaRef] = []
    for item in input_message_data_items(content, MEDIA_DATA_KEY):
        try:
            refs.append(MediaRef.model_validate(item))
        except ValidationError:
            logger.warning("Ignoring malformed media reference", extra={"media_item": item})
    return refs


# Content-addressed, so an entry never goes stale; bounded so a busy process
# cannot pin every attachment it has ever seen. Keyed per sandbox so one
# thread's attachment is never served as another thread's.
_CACHE: OrderedDict[str, bytes] = OrderedDict()
_CACHE_BYTES = 0


def _cache_key(backend: SandboxBackendProtocol, path: str) -> str:
    sandbox = getattr(backend, "current", backend)
    return f"{getattr(sandbox, 'id', None) or id(sandbox)}:{path}"


def _cache_get(path: str) -> bytes | None:
    data = _CACHE.get(path)
    if data is not None:
        _CACHE.move_to_end(path)
    return data


def _cache_put(path: str, data: bytes) -> None:
    global _CACHE_BYTES
    if len(data) > _CACHE_LIMIT_BYTES:
        return
    if path in _CACHE:
        _CACHE_BYTES -= len(_CACHE.pop(path))
    _CACHE[path] = data
    _CACHE_BYTES += len(data)
    while _CACHE_BYTES > _CACHE_LIMIT_BYTES:
        _, evicted = _CACHE.popitem(last=False)
        _CACHE_BYTES -= len(evicted)
