"""Where offloaded conversation images live and which threads may show them.

Shared by the agent middleware that stores them and the dashboard route that
serves them. Images are files named ``<uuid hex>.<ext>``: cloud threads keep
them in the thread's sandbox under ``.open-swe/images``, desktop threads under
the app's per-thread artifacts directory. ``ImageStore`` is the seam for moving
them to object storage later without touching the message format.
"""

import asyncio
import base64
import binascii
import logging
import posixpath
import re
import shlex
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from deepagents.backends.protocol import SandboxBackendProtocol

logger = logging.getLogger(__name__)

SANDBOX_IMAGE_DIR = ".open-swe/images"

IMAGE_EXTENSIONS: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}
_MIME_TYPES = {extension: mime for mime, extension in IMAGE_EXTENSIONS.items()}
_IMAGE_NAME_RE = re.compile(r"^(?P<id>[0-9a-f]{32})\.(?P<ext>png|jpg|gif|webp)$")

type ContentBlock = dict[str, object]


def image_file_name(image_id: str, mime_type: str) -> str | None:
    """The stored file name for an image, or ``None`` for an unsupported type."""
    extension = IMAGE_EXTENSIONS.get(mime_type)
    if extension is None or not re.fullmatch(r"[0-9a-f]{32}", image_id):
        return None
    return f"{image_id}.{extension}"


def parse_image_name(name: str) -> tuple[str, str] | None:
    """``(image_id, mime_type)`` for a stored file name, or ``None``.

    Names are the only path segment built from request input, so anything that
    is not exactly ``<hex id>.<known extension>`` is rejected before a path is made.
    """
    match = _IMAGE_NAME_RE.fullmatch(name)
    if match is None:
        return None
    return match.group("id"), _MIME_TYPES[match.group("ext")]


def image_owner_threads(thread_id: str, continued_from_thread_id: object) -> tuple[str, ...]:
    """Threads whose stored images ``thread_id`` may show, in lookup order.

    A private continuation copies the transcript of the collaborative thread it
    was made from, so its messages reference images stored with that thread.
    """
    owners = [thread_id] if thread_id else []
    if (
        isinstance(continued_from_thread_id, str)
        and continued_from_thread_id
        and continued_from_thread_id not in owners
    ):
        owners.append(continued_from_thread_id)
    return tuple(owners)


class ImageStore(Protocol):
    async def put(self, name: str, content: bytes) -> None: ...

    async def get(self, name: str) -> bytes | None:
        """The stored bytes, or ``None`` when no such image exists."""
        ...


def as_image_block(item: object) -> ContentBlock | None:
    if isinstance(item, dict) and item.get("type") == "image":
        return item
    return None


def inline_image(block: ContentBlock) -> tuple[str, str] | None:
    """``(base64, mime_type)`` for an image block that still carries its bytes."""
    encoded = block.get("base64")
    mime_type = block.get("mime_type")
    if isinstance(encoded, str) and encoded and isinstance(mime_type, str) and mime_type:
        return encoded, mime_type
    return None


def image_reference(block: ContentBlock) -> tuple[str, str] | None:
    """``(file_id, mime_type)`` for an offloaded image block."""
    file_id = block.get("file_id")
    mime_type = block.get("mime_type")
    if (
        isinstance(file_id, str)
        and file_id
        and isinstance(mime_type, str)
        and "base64" not in block
    ):
        return file_id, mime_type
    return None


def has_inline_image(content: object) -> bool:
    return isinstance(content, list) and any(
        (block := as_image_block(item)) is not None and inline_image(block) is not None
        for item in content
    )


async def offload_image_blocks(content: list[object], store: ImageStore) -> list[object] | None:
    """``content`` with its inline images stored and referenced by ``file_id``.

    Returns ``None`` when nothing was offloaded. Images the store cannot take
    (unknown type, undecodable bytes) stay inline.
    """
    result: list[object] = []
    offloaded = False
    for item in content:
        block = as_image_block(item)
        inline = inline_image(block) if block is not None else None
        if block is None or inline is None:
            result.append(item)
            continue
        encoded, mime_type = inline
        image_id = uuid.uuid4().hex
        name = image_file_name(image_id, mime_type)
        if name is None:
            result.append(item)
            continue
        try:
            raw = base64.b64decode(encoded, validate=True)
        except binascii.Error, ValueError:
            result.append(item)
            continue
        await store.put(name, raw)
        reference: ContentBlock = {
            key: value for key, value in block.items() if key not in {"base64", "data", "url"}
        }
        reference["file_id"] = image_id
        result.append(reference)
        offloaded = True
    return result if offloaded else None


class SandboxImageStore:
    """Images kept next to the thread's working tree in its sandbox."""

    def __init__(self, backend: SandboxBackendProtocol, work_dir: str) -> None:
        self._backend = backend
        self._dir = posixpath.join(work_dir, SANDBOX_IMAGE_DIR)

    def path_for(self, name: str) -> str:
        return posixpath.join(self._dir, name)

    async def put(self, name: str, content: bytes) -> None:
        await self._backend.aexecute(f"mkdir -p {shlex.quote(self._dir)}")
        [response] = await self._backend.aupload_files([(self.path_for(name), content)])
        if response.error:
            raise RuntimeError(f"sandbox image upload failed: {response.error}")

    async def get(self, name: str) -> bytes | None:
        [response] = await self._backend.adownload_files([self.path_for(name)])
        return response.content


class LocalImageStore:
    """Images kept on the host, for desktop threads that have no sandbox."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def path_for(self, name: str) -> Path:
        return self._root / name

    async def put(self, name: str, content: bytes) -> None:
        def write() -> None:
            self._root.mkdir(parents=True, exist_ok=True)
            self.path_for(name).write_bytes(content)

        await asyncio.to_thread(write)

    async def get(self, name: str) -> bytes | None:
        def read() -> bytes | None:
            try:
                return self.path_for(name).read_bytes()
            except FileNotFoundError:
                return None

        return await asyncio.to_thread(read)


async def open_image_store(thread_id: str, *, desktop: bool = False) -> ImageStore:
    """The store holding ``thread_id``'s images.

    Connecting to a sandbox may resume a stopped one, so callers on a latency
    budget should cache what they read.
    """
    if desktop:
        from agent.desktop import desktop_images_dir

        return LocalImageStore(desktop_images_dir(thread_id))
    # Deferred: the sandbox stack pulls in the provider SDKs, which the webapp
    # should not import at startup.
    from agent.sandboxes.paths import resolve_sandbox_work_dir
    from agent.sandboxes.state import get_sandbox_backend

    backend = await get_sandbox_backend(thread_id)
    return SandboxImageStore(backend, await resolve_sandbox_work_dir(backend))


async def provision_image_store(thread_id: str, *, workspace_slug: str | None) -> ImageStore:
    """The store for a cloud thread, creating its sandbox when it has none yet.

    Used before a run exists so its input can already carry references. The
    sandbox id is persisted to the thread, so the run reconnects to this
    sandbox instead of provisioning another.
    """
    from agent.sandboxes.lifecycle import ensure_sandbox_for_thread
    from agent.sandboxes.paths import resolve_sandbox_work_dir

    backend = await ensure_sandbox_for_thread(thread_id, workspace_slug=workspace_slug)
    return SandboxImageStore(backend, await resolve_sandbox_work_dir(backend))
