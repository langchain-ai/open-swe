"""Where offloaded conversation images live and which threads may show them.

Shared by the agent middleware that stores them and the dashboard route that
serves them. Images are files named ``<uuid hex>.<ext>``: cloud threads keep
them in the thread's sandbox under ``.open-swe/images``, desktop threads under
the app's per-thread artifacts directory. ``ImageStore`` is the seam for moving
them to object storage later without touching the message format.
"""

import asyncio
import logging
import posixpath
import re
import shlex
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
