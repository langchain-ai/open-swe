"""Shared sandbox state used by server and middleware."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from deepagents.backends.protocol import (
    EditResult,
    ExecuteOffloadResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
    execute_accepts_timeout,
)
from deepagents.backends.sandbox import BaseSandbox
from langgraph.config import get_config
from langgraph_sdk import get_client

from .sandbox import create_sandbox

logger = logging.getLogger(__name__)


class SandboxBackendProxy(BaseSandbox):
    """Stable per-thread backend handle whose target can be replaced.

    Subclasses ``BaseSandbox`` (not just the protocol) so ``FilesystemMiddleware``
    recognizes it as capture-at-source capable: its ``_resolve_capture`` gates the
    ``execute`` offload path on ``isinstance(backend, BaseSandbox)``. Without this
    the tool falls back to plain ``execute`` and the command's entire stdout is
    pulled into the worker process, bypassing the in-sandbox size cap.
    """

    def __init__(
        self,
        backend: SandboxBackendProtocol | None = None,
        *,
        thread_id: str | None = None,
        reconnect: Callable[[], Awaitable[SandboxBackendProtocol]] | None = None,
    ) -> None:
        self._backend = backend
        self._thread_id = thread_id
        self._reconnect = reconnect
        self._lock: asyncio.Lock | None = None

    @property
    def current(self) -> SandboxBackendProtocol:
        return self._get_backend()

    @property
    def id(self) -> str:
        return self._get_backend().id

    def replace_backend(self, backend: SandboxBackendProtocol) -> None:
        self._backend = backend

    @property
    def has_backend(self) -> bool:
        return self._backend is not None

    def set_reconnect(
        self,
        reconnect: Callable[[], Awaitable[SandboxBackendProtocol]] | None,
    ) -> None:
        self._reconnect = reconnect

    def _get_backend(self) -> SandboxBackendProtocol:
        if self._backend is None:
            suffix = f" for thread {self._thread_id}" if self._thread_id else ""
            raise RuntimeError(f"No sandbox backend cached{suffix}")
        return self._backend

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _aget_backend(self) -> SandboxBackendProtocol:
        if self._backend is not None:
            return self._backend
        if not self._thread_id:
            raise RuntimeError("No sandbox backend cached")

        async with self._get_lock():
            if self._backend is not None:
                return self._backend

            if self._reconnect is not None:
                logger.info("Reconnecting sandbox backend for thread %s", self._thread_id)
                sandbox_backend = await self._reconnect()
                self._backend = unwrap_sandbox_backend(sandbox_backend)
                return self._backend

            sandbox_id = await get_sandbox_id_from_metadata(self._thread_id)
            if not sandbox_id:
                raise ValueError(f"Missing sandbox_id in thread metadata for {self._thread_id}")

            logger.info("Reconnecting sandbox backend for thread %s from metadata", self._thread_id)
            self._backend = await create_sandbox(sandbox_id)
            SANDBOX_BACKENDS[self._thread_id] = self
            return self._backend

    def ls(self, path: str) -> LsResult:
        return self._get_backend().ls(path)

    async def als(self, path: str) -> LsResult:
        return await (await self._aget_backend()).als(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self._get_backend().read(file_path, offset, limit)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return await (await self._aget_backend()).aread(file_path, offset, limit)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return self._get_backend().grep(pattern, path, glob, max_count=max_count)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return await (await self._aget_backend()).agrep(pattern, path, glob, max_count=max_count)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self._get_backend().glob(pattern, path)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return await (await self._aget_backend()).aglob(pattern, path)

    def write(self, file_path: str, content: str) -> WriteResult:
        return self._get_backend().write(file_path, content)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return await (await self._aget_backend()).awrite(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return self._get_backend().edit(file_path, old_string, new_string, replace_all)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return await (await self._aget_backend()).aedit(
            file_path, old_string, new_string, replace_all
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self._get_backend().upload_files(files)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return await (await self._aget_backend()).aupload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self._get_backend().download_files(paths)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return await (await self._aget_backend()).adownload_files(paths)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return self._get_backend().execute(command, timeout=timeout)

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return await (await self._aget_backend()).aexecute(command, timeout=timeout)

    def execute_with_offload(
        self,
        command: str,
        capture_path: str,
        *,
        max_inline_bytes: int,
        max_capture_bytes: int | None = None,
        timeout: int | None = None,
    ) -> ExecuteOffloadResult:
        # Delegate to the live backend so each provider's own capture-offload
        # behavior (enable_capture_offload, shell wrapper) is honored.
        backend = self._get_backend()
        offload = getattr(backend, "execute_with_offload", None)
        if offload is None:
            return ExecuteOffloadResult(
                offloaded=False, response=self._plain(backend, command, timeout)
            )
        return offload(
            command,
            capture_path,
            max_inline_bytes=max_inline_bytes,
            max_capture_bytes=max_capture_bytes,
            timeout=timeout,
        )

    async def aexecute_with_offload(
        self,
        command: str,
        capture_path: str,
        *,
        max_inline_bytes: int,
        max_capture_bytes: int | None = None,
        timeout: int | None = None,  # noqa: ASYNC109 - forwarded to backend, not an asyncio contract
    ) -> ExecuteOffloadResult:
        backend = await self._aget_backend()
        offload = getattr(backend, "aexecute_with_offload", None)
        if offload is None:
            return ExecuteOffloadResult(
                offloaded=False, response=await self._aplain(backend, command, timeout)
            )
        return await offload(
            command,
            capture_path,
            max_inline_bytes=max_inline_bytes,
            max_capture_bytes=max_capture_bytes,
            timeout=timeout,
        )

    @staticmethod
    def _plain(
        backend: SandboxBackendProtocol, command: str, timeout: int | None
    ) -> ExecuteResponse:
        if timeout is not None and execute_accepts_timeout(type(backend)):
            return backend.execute(command, timeout=timeout)
        return backend.execute(command)

    @staticmethod
    async def _aplain(
        backend: SandboxBackendProtocol, command: str, timeout: int | None
    ) -> ExecuteResponse:
        if timeout is not None and execute_accepts_timeout(type(backend)):
            return await backend.aexecute(command, timeout=timeout)
        return await backend.aexecute(command)


# Thread ID -> stable SandboxBackendProxy, shared between server.py and middleware.
SANDBOX_BACKENDS: dict[str, SandboxBackendProxy] = {}


def unwrap_sandbox_backend(sandbox_backend: SandboxBackendProtocol) -> SandboxBackendProtocol:
    if isinstance(sandbox_backend, SandboxBackendProxy):
        return sandbox_backend.current
    return sandbox_backend


def set_sandbox_backend(
    thread_id: str,
    sandbox_backend: SandboxBackendProtocol,
) -> SandboxBackendProxy:
    if isinstance(sandbox_backend, SandboxBackendProxy):
        SANDBOX_BACKENDS[thread_id] = sandbox_backend
        return sandbox_backend

    existing = SANDBOX_BACKENDS.get(thread_id)
    if isinstance(existing, SandboxBackendProxy):
        existing.replace_backend(sandbox_backend)
        return existing

    proxy = SandboxBackendProxy(sandbox_backend, thread_id=thread_id)
    SANDBOX_BACKENDS[thread_id] = proxy
    return proxy


def get_or_create_sandbox_backend_proxy(
    thread_id: str,
    *,
    reconnect: Callable[[], Awaitable[SandboxBackendProtocol]] | None = None,
) -> SandboxBackendProxy:
    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    if sandbox_backend:
        sandbox_backend.set_reconnect(reconnect)
        return sandbox_backend

    sandbox_backend = SandboxBackendProxy(thread_id=thread_id, reconnect=reconnect)
    SANDBOX_BACKENDS[thread_id] = sandbox_backend
    return sandbox_backend


def clear_sandbox_backend(thread_id: str) -> None:
    SANDBOX_BACKENDS.pop(thread_id, None)


async def get_sandbox_id_from_metadata(thread_id: str) -> str | None:
    """Fetch sandbox_id from thread metadata."""
    try:
        config = get_config()
        metadata = config.get("metadata", {})
        if isinstance(metadata, dict):
            sandbox_id = metadata.get("sandbox_id")
            if isinstance(sandbox_id, str):
                return sandbox_id
    except Exception:
        logger.debug(
            "Failed to read inline thread metadata for sandbox; falling back to live lookup",
            exc_info=True,
        )

    try:
        client = get_client()
        thread = await client.threads.get(thread_id)
    except Exception:
        logger.exception("Failed to fetch live thread metadata for sandbox")
        return None

    metadata = thread.get("metadata", {}) if isinstance(thread, dict) else {}
    sandbox_id = metadata.get("sandbox_id") if isinstance(metadata, dict) else None
    return sandbox_id if isinstance(sandbox_id, str) else None


async def get_sandbox_backend(thread_id: str) -> SandboxBackendProxy:
    """Get sandbox backend from cache, or connect using thread metadata."""
    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    if sandbox_backend and sandbox_backend.has_backend:
        return sandbox_backend

    sandbox_id = await get_sandbox_id_from_metadata(thread_id)
    if not sandbox_id:
        raise ValueError(f"Missing sandbox_id in thread metadata for {thread_id}")

    sandbox_backend = await create_sandbox(sandbox_id)
    return set_sandbox_backend(thread_id, sandbox_backend)
