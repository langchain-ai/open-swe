"""Read-only adapter for virtual backend routes."""

from __future__ import annotations

from deepagents.backends.protocol import (
    BackendProtocol,
    FileDownloadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
)

_SYNC_UNSUPPORTED = "ReadOnlyBackend is async-only; use the a-prefixed method instead."


class ReadOnlyBackend(BackendProtocol):
    """Delegate backend reads while rejecting inherited mutation operations."""

    def __init__(self, backend: BackendProtocol) -> None:
        self._backend = backend

    def ls(self, path: str) -> LsResult:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def als(self, path: str) -> LsResult:
        return await self._backend.als(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return await self._backend.aread(file_path, offset, limit)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return await self._backend.agrep(pattern, path, glob, max_count=max_count)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return await self._backend.aglob(pattern, path)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return await self._backend.adownload_files(paths)
