"""Shared sandbox state used by server and middleware."""

from __future__ import annotations

import asyncio
import logging

from deepagents.backends.protocol import (
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)
from langgraph.config import get_config

from .sandbox import create_sandbox

logger = logging.getLogger(__name__)


class SandboxBackendProxy(SandboxBackendProtocol):
    """Stable per-thread backend handle whose target can be replaced."""

    def __init__(self, backend: SandboxBackendProtocol) -> None:
        self._backend = backend

    @property
    def current(self) -> SandboxBackendProtocol:
        return self._backend

    @property
    def id(self) -> str:
        return self._backend.id

    def replace_backend(self, backend: SandboxBackendProtocol) -> None:
        self._backend = backend

    def ls(self, path: str) -> LsResult:
        return self._backend.ls(path)

    async def als(self, path: str) -> LsResult:
        return await self._backend.als(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self._backend.read(file_path, offset, limit)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return await self._backend.aread(file_path, offset, limit)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        return self._backend.grep(pattern, path, glob)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        return await self._backend.agrep(pattern, path, glob)

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        return self._backend.glob(pattern, path)

    async def aglob(self, pattern: str, path: str = "/") -> GlobResult:
        return await self._backend.aglob(pattern, path)

    def write(self, file_path: str, content: str) -> WriteResult:
        return self._backend.write(file_path, content)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return await self._backend.awrite(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return self._backend.edit(file_path, old_string, new_string, replace_all)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return await self._backend.aedit(file_path, old_string, new_string, replace_all)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self._backend.upload_files(files)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return await self._backend.aupload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self._backend.download_files(paths)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return await self._backend.adownload_files(paths)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return self._backend.execute(command, timeout=timeout)

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return await self._backend.aexecute(command, timeout=timeout)


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

    proxy = SandboxBackendProxy(sandbox_backend)
    SANDBOX_BACKENDS[thread_id] = proxy
    return proxy


def clear_sandbox_backend(thread_id: str) -> None:
    SANDBOX_BACKENDS.pop(thread_id, None)


async def get_sandbox_id_from_metadata(thread_id: str) -> str | None:
    """Fetch sandbox_id from thread metadata."""
    try:
        config = get_config()
    except Exception:
        logger.exception("Failed to read thread metadata for sandbox")
        return None
    metadata = config.get("metadata", {})
    if not isinstance(metadata, dict):
        return None
    sandbox_id = metadata.get("sandbox_id")
    return sandbox_id if isinstance(sandbox_id, str) else None


async def get_sandbox_backend(thread_id: str) -> SandboxBackendProxy:
    """Get sandbox backend from cache, or connect using thread metadata."""
    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    if sandbox_backend:
        return sandbox_backend

    sandbox_id = await get_sandbox_id_from_metadata(thread_id)
    if not sandbox_id:
        raise ValueError(f"Missing sandbox_id in thread metadata for {thread_id}")

    sandbox_backend = await asyncio.to_thread(create_sandbox, sandbox_id)
    return set_sandbox_backend(thread_id, sandbox_backend)


def get_sandbox_backend_sync(thread_id: str) -> SandboxBackendProxy:
    """Sync wrapper for get_sandbox_backend."""
    return asyncio.run(get_sandbox_backend(thread_id))
