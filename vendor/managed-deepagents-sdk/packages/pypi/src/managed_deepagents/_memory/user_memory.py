"""Caller-scoped Context Hub backend for ``/memories/user/``."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from deepagents.backends import ContextHubBackend, FilesystemBackend
from deepagents.backends.protocol import BackendProtocol

from managed_deepagents._identity_runtime import (
    build_runtime_identity,
    resolve_runtime_configurable,
)
from managed_deepagents._memory.policy import is_slack_direct_message, resolve_user_memory_principal

if TYPE_CHECKING:
    from deepagents.backends.protocol import (
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

_USER_REPO_SEPARATOR = "--u--"
_USER_REPO_DIGEST_LENGTH = 24


def user_memory_repo_handle(deploy_handle: str, user_id: str) -> str:
    """Return an opaque child-repo handle for one deployment and user."""
    digest = sha256()
    digest.update(deploy_handle.encode())
    digest.update(b"\0")
    digest.update(user_id.encode())
    return f"{deploy_handle}{_USER_REPO_SEPARATOR}{digest.hexdigest()[:_USER_REPO_DIGEST_LENGTH]}"


def can_mount_user_memory(run_config: Mapping[str, object] | None, context: object = None) -> bool:
    """Default access requires a trusted person in a Slack one-to-one DM."""
    configurable = resolve_runtime_configurable(run_config)
    identity = build_runtime_identity(configurable)
    return (
        identity is not None
        and identity["user"]["kind"] == "person"
        and is_slack_direct_message(context, configurable)
    )


class UserMemoryBackend(BackendProtocol):
    """Bind each backend operation to the active caller's memory repo."""

    def __init__(
        self,
        *,
        deploy_handle: str,
        filesystem_root: str | None,
        run_config: Mapping[str, object] | None,
        asserted_user_id: str | None = None,
        allowed_user_id: str | None = None,
    ) -> None:
        self._deploy_handle = deploy_handle
        self._filesystem_root = filesystem_root
        self._run_config = run_config
        self._asserted_user_id = asserted_user_id
        self._allowed_user_id = allowed_user_id
        self._repos: dict[str, BackendProtocol] = {}
        self._lock = threading.Lock()

    def ls(self, path: str = "/") -> LsResult:
        return self._backend().ls(path)

    async def als(self, path: str = "/") -> LsResult:
        return await self._backend().als(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self._backend().read(file_path, offset=offset, limit=limit)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return await self._backend().aread(file_path, offset=offset, limit=limit)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        # Forward the cap only when set, matching the TypeScript adapter's call arity.
        if max_count is not None:
            return self._backend().grep(pattern, path, glob, max_count=max_count)
        return self._backend().grep(pattern, path, glob)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        if max_count is not None:
            return await self._backend().agrep(pattern, path, glob, max_count=max_count)
        return await self._backend().agrep(pattern, path, glob)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self._backend().glob(pattern, path)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return await self._backend().aglob(pattern, path)

    def write(self, file_path: str, content: str) -> WriteResult:
        return self._backend().write(file_path, content)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return await self._backend().awrite(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return self._backend().edit(file_path, old_string, new_string, replace_all=replace_all)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return await self._backend().aedit(
            file_path, old_string, new_string, replace_all=replace_all
        )

    def delete(self, file_path: str) -> DeleteResult:
        return self._backend().delete(file_path)

    async def adelete(self, file_path: str) -> DeleteResult:
        return await self._backend().adelete(file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self._backend().upload_files(files)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return await self._backend().aupload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self._backend().download_files(paths)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return await self._backend().adownload_files(paths)

    def _backend(self) -> BackendProtocol:
        handle = self._repo_handle()
        with self._lock:
            existing = self._repos.get(handle)
            if existing is not None:
                return existing
            backend = self._open(handle)
            self._repos[handle] = backend
            return backend

    def _repo_handle(self) -> str:
        """The repo for the authenticated caller, and only ever for them.

        The declared ``id`` on a user layer is an assertion, not a selector: the
        handle is keyed on the principal the trusted envelope resolved, and a
        mismatch fails the run. Mirrors ``resolve_owner_id`` for credentials, so
        an authored id can never reach another caller's memory.
        """
        principal = resolve_user_memory_principal(resolve_runtime_configurable(self._run_config))
        if principal is None:
            raise ValueError("User memory requires a trusted caller identity")
        authenticated = principal.id
        if self._asserted_user_id is not None and self._asserted_user_id != authenticated:
            raise ValueError("user memory layer id must match the authenticated principal")
        if self._allowed_user_id is None or authenticated != self._allowed_user_id:
            raise ValueError("User memory requires the allowed caller for this run")
        return user_memory_repo_handle(self._deploy_handle, authenticated)

    def _open(self, handle: str) -> BackendProtocol:
        if self._filesystem_root is not None:
            root_dir = str(Path(self._filesystem_root) / "__users__" / handle)
            Path(root_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
            return FilesystemBackend(root_dir=root_dir, virtual_mode=True)
        return ContextHubBackend(handle)
