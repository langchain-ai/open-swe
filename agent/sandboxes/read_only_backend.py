"""Read-only adapter for virtual backend routes."""

from deepagents.backends.protocol import (
    BackendProtocol,
    FileDownloadResponse,
    FileInfo,
    GlobResult,
    GrepMatch,
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


class PrefixedReadOnlyBackend(ReadOnlyBackend):
    """Restore a route prefix when delegating reads to an absolute-path backend."""

    def __init__(self, backend: BackendProtocol, prefix: str, *, reattach: bool = True) -> None:
        super().__init__(backend)
        self._prefix = prefix.rstrip("/")
        self._reattach = reattach

    def _full_path(self, path: str) -> str:
        return f"{self._prefix}/{path.lstrip('/')}"

    def _delegate_path(self, path: str) -> str:
        return self._full_path(path) if self._reattach else path

    def _without_prefix(self, path: str) -> str:
        if not self._reattach:
            return path
        if path == self._prefix:
            return "/"
        return path.removeprefix(f"{self._prefix}/") or "/"

    def _with_error_path(self, error: str, path: str) -> str:
        if self._reattach:
            return error
        return error.replace(f"'{path}'", f"'{self._full_path(path)}'")

    def _relative_file_info(self, file_info: FileInfo) -> FileInfo:
        return {**file_info, "path": self._without_prefix(file_info["path"])}

    def _relative_grep_match(self, match: GrepMatch) -> GrepMatch:
        return {**match, "path": self._without_prefix(match["path"])}

    async def als(self, path: str) -> LsResult:
        result = await self._backend.als(self._delegate_path(path))
        if result.error:
            return LsResult(error=self._with_error_path(result.error, path))
        return LsResult(entries=[self._relative_file_info(entry) for entry in result.entries or []])

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        result = await self._backend.aread(self._delegate_path(file_path), offset, limit)
        if result.error:
            return ReadResult(
                error=self._with_error_path(result.error, file_path),
                no_lines_requested=result.no_lines_requested,
            )
        return result

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        result = await self._backend.agrep(
            pattern,
            self._delegate_path(path) if path is not None else None,
            glob,
            max_count=max_count,
        )
        if result.error:
            return GrepResult(error=self._with_error_path(result.error, path or "/"))
        return GrepResult(
            matches=[self._relative_grep_match(match) for match in result.matches or []],
            truncated=result.truncated,
        )

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        result = await self._backend.aglob(
            pattern,
            self._delegate_path(path) if path is not None else None,
        )
        if result.error:
            return GlobResult(error=self._with_error_path(result.error, path or "/"))
        return GlobResult(
            matches=[self._relative_file_info(match) for match in result.matches or []],
            truncated=result.truncated,
            truncation_reason=result.truncation_reason,
        )
