"""Harbor eval trial filesystem helpers.

With ``virtual_mode=True`` and ``root_dir=/app``, deepagents resolves
``/app/answer.txt`` to ``/app/app/answer.txt``. Normalize so both StateBackend-
style ``/answer.txt`` and disk-absolute ``/app/answer.txt`` land at
``{app_dir}/answer.txt`` for Harbor verifiers.
"""

from __future__ import annotations

import asyncio
import posixpath
from pathlib import Path
from typing import TypeVar, cast

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    DeleteResult,
    EditResult,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from managed_deepagents._memory.context_io import result_value

_EntryT = TypeVar("_EntryT")


def normalize_eval_virtual_path(file_path: str, app_dir: str) -> str:
    """Map Harbor/disk-absolute paths onto the virtual FS rooted at ``app_dir``."""
    root = _collapse_leading_slashes(app_dir).rstrip("/") or "/"
    normalized_path = _collapse_leading_slashes(file_path)
    if root == "/":
        return normalized_path if normalized_path.startswith("/") else f"/{normalized_path}"
    if normalized_path in (root, f"{root}/"):
        return "/"
    if normalized_path.startswith(f"{root}/"):
        return _collapse_leading_slashes(normalized_path[len(root) :]) or "/"
    return normalized_path if normalized_path.startswith("/") else f"/{normalized_path}"


def _collapse_leading_slashes(value: str) -> str:
    return f"/{value.lstrip('/')}" if value.startswith("/") else value


def _is_skills_path(file_path: str, app_dir: str) -> bool:
    normalized = posixpath.normpath(normalize_eval_virtual_path(file_path, app_dir))
    return normalized == "/skills" or normalized.startswith("/skills/")


class _FilteredEvalFilesystemBackend(BackendProtocol):
    """Hide eval's authored skills tree behind the selected ``/skills/`` route."""

    def __init__(self, backend: FilesystemBackend, app_dir: str) -> None:
        self._backend = backend
        self._app_dir = app_dir

    def _visible(self, entries: list[_EntryT] | None) -> list[_EntryT]:
        if not isinstance(entries, list):
            return []
        return [
            entry
            for entry in entries
            if not (
                isinstance(path := result_value(entry, "path"), str)
                and _is_skills_path(path, self._app_dir)
            )
        ]

    def ls(self, path: str = "/") -> LsResult:
        if _is_skills_path(path, self._app_dir):
            return LsResult(entries=[])
        source = self._backend.ls(path)
        return LsResult(
            error=source.error,
            entries=self._visible(source.entries),
        )

    async def als(self, path: str = "/") -> LsResult:
        return await asyncio.to_thread(self.ls, path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        if _is_skills_path(file_path, self._app_dir):
            return ReadResult(error="file_not_found")
        return self._backend.read(file_path, offset, limit)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return await asyncio.to_thread(self.read, file_path, offset, limit)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        if _is_skills_path(path or "/", self._app_dir):
            return GlobResult(matches=[])
        source = self._backend.glob(pattern, path)
        return GlobResult(
            error=source.error,
            matches=self._visible(source.matches),
            truncated=source.truncated,
            truncation_reason=source.truncation_reason,
        )

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return await asyncio.to_thread(self.glob, pattern, path)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        if _is_skills_path(path or "/", self._app_dir):
            return GrepResult(matches=[])
        source = self._backend.grep(pattern, path, glob, max_count=max_count)
        return GrepResult(
            error=source.error,
            matches=self._visible(source.matches),
            truncated=source.truncated,
        )

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return await asyncio.to_thread(self.grep, pattern, path, glob, max_count=max_count)

    def write(self, file_path: str, content: str) -> WriteResult:
        if _is_skills_path(file_path, self._app_dir):
            return WriteResult(error="permission_denied")
        return self._backend.write(file_path, content)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return await asyncio.to_thread(self.write, file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        if _is_skills_path(file_path, self._app_dir):
            return EditResult(error="permission_denied")
        return self._backend.edit(file_path, old_string, new_string, replace_all)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return await asyncio.to_thread(self.edit, file_path, old_string, new_string, replace_all)

    def delete(self, file_path: str) -> DeleteResult:
        if _is_skills_path(file_path, self._app_dir):
            return DeleteResult(error="permission_denied")
        return self._backend.delete(file_path)

    async def adelete(self, file_path: str) -> DeleteResult:
        return await asyncio.to_thread(self.delete, file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        results: list[FileUploadResponse | None] = [None] * len(files)
        visible: list[tuple[str, bytes]] = []
        indexes: list[int] = []
        for index, (path, content) in enumerate(files):
            if _is_skills_path(path, self._app_dir):
                results[index] = FileUploadResponse(path=path, error="permission_denied")
            else:
                visible.append((path, content))
                indexes.append(index)
        if visible:
            uploaded = self._backend.upload_files(visible)
            for index, item in zip(indexes, uploaded, strict=True):
                results[index] = item
        return cast("list[FileUploadResponse]", results)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return await asyncio.to_thread(self.upload_files, files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        results: list[FileDownloadResponse | None] = [None] * len(paths)
        visible: list[str] = []
        indexes: list[int] = []
        for index, path in enumerate(paths):
            if _is_skills_path(path, self._app_dir):
                results[index] = FileDownloadResponse(
                    path=path, content=None, error="file_not_found"
                )
            else:
                visible.append(path)
                indexes.append(index)
        if visible:
            downloaded = self._backend.download_files(visible)
            for index, item in zip(indexes, downloaded, strict=True):
                results[index] = item
        return cast("list[FileDownloadResponse]", results)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return await asyncio.to_thread(self.download_files, paths)


def create_eval_trial_filesystem_backend(
    filesystem_backend_cls: type[FilesystemBackend],
    app_dir: str,
    *,
    hide_skills: bool = False,
) -> BackendProtocol:
    """Build a disk backend for Harbor eval trials under ``app_dir``."""
    root_dir = app_dir.strip() or "/app"
    backend = filesystem_backend_cls(root_dir=root_dir, virtual_mode=True)
    original = backend._resolve_path  # noqa: SLF001

    def _resolve_path(key: str) -> Path:
        return original(normalize_eval_virtual_path(key, root_dir))

    # Instance-level patch of the private upstream resolver; assigning through
    # ``setattr`` keeps the bound-method attribute type out of the way.
    setattr(backend, "_resolve_path", _resolve_path)  # noqa: B010
    return _FilteredEvalFilesystemBackend(backend, root_dir) if hide_skills else backend
