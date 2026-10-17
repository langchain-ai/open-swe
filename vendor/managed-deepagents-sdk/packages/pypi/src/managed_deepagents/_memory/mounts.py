"""Composite backend mounts for instructions, skills, and memory views."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from deepagents.backends.protocol import BackendProtocol

from managed_deepagents._memory.context_io import result_value, set_result_path
from managed_deepagents._memory.paths import (
    assert_safe_mounted_path,
    ensure_absolute,
    strip_trailing_slash,
    trim_slashes,
)

if TYPE_CHECKING:
    from deepagents import FilesystemPermission
    from deepagents.backends import CompositeBackend, StateBackend
    from deepagents.backends.protocol import (
        EditResult,
        FileDownloadResponse,
        FileUploadResponse,
        GlobResult,
        GrepResult,
        LsResult,
        ReadResult,
        WriteResult,
    )

_ResultT = TypeVar("_ResultT")


def managed_context_backend(
    context_backend: BackendProtocol,
    sandbox_backend: BackendProtocol | None,
    composite_backend_cls: type[CompositeBackend],
    state_backend_cls: type[StateBackend],
    *,
    mount_agent_memory: bool = False,
    user_memory_backend: BackendProtocol | None = None,
    selected_skills_backend: BackendProtocol | None = None,
) -> CompositeBackend:
    routes = managed_context_routes(
        context_backend,
        mount_agent_memory=mount_agent_memory,
        user_memory_backend=user_memory_backend,
        selected_skills_backend=selected_skills_backend,
    )
    default_backend = sandbox_backend if sandbox_backend is not None else state_backend_cls()
    return composite_backend_cls(
        default=default_backend,
        routes=routes,
    )


def managed_selected_skills_backend(
    default_backend: BackendProtocol | None,
    selected_skills_backend: BackendProtocol,
    composite_backend_cls: type[CompositeBackend],
    state_backend_cls: type[StateBackend],
) -> CompositeBackend:
    return composite_backend_cls(
        default=default_backend if default_backend is not None else state_backend_cls(),
        routes={"/skills/": selected_skills_backend},
    )


def managed_context_routes(
    context_backend: BackendProtocol,
    *,
    mount_agent_memory: bool = False,
    user_memory_backend: BackendProtocol | None = None,
    selected_skills_backend: BackendProtocol | None = None,
) -> dict[str, BackendProtocol]:
    routes: dict[str, BackendProtocol] = {
        "/instructions.md": MountedPathBackend.file(
            context_backend, "instructions.md", "/instructions.md"
        ),
        "/skills/": selected_skills_backend
        if selected_skills_backend is not None
        else MountedPathBackend.directory(context_backend, "skills", "/skills", read_only=True),
    }
    if mount_agent_memory:
        # Deployment-shared procedural memory on the deploy Hub repo.
        routes["/memories/agent/"] = MountedPathBackend.directory(
            context_backend, "memories/agent", "/memories/agent"
        )
    if user_memory_backend is not None:
        routes["/memories/user/"] = MountedPathBackend.directory(
            user_memory_backend, "", "/memories/user"
        )
    return routes


class MountedPathBackend(BackendProtocol):
    """Backend view that mounts a source file or directory at another path.

    ``delete`` is intentionally not overridden: the inherited
    ``BackendProtocol.delete`` raises ``NotImplementedError``, which
    ``CompositeBackend`` reports as an unsupported-delete ``DeleteResult``.
    Context Hub mounts (instructions, skills, memory) are never deleted through
    the agent filesystem, matching the TypeScript runtime, whose mounts expose
    no delete operation.
    """

    def __init__(
        self,
        context_backend: BackendProtocol,
        *,
        repo_prefix: str | None = None,
        visible_prefix: str | None = None,
        repo_file: str | None = None,
        visible_file: str | None = None,
        read_only: bool = False,
    ) -> None:
        self._backend = context_backend
        self._repo_prefix = trim_slashes(repo_prefix or "")
        self._visible_prefix = strip_trailing_slash(ensure_absolute(visible_prefix or ""))
        self._repo_file = trim_slashes(repo_file) if repo_file is not None else None
        self._visible_file = ensure_absolute(visible_file) if visible_file else None
        self._read_only = read_only

    @classmethod
    def directory(
        cls,
        context_backend: BackendProtocol,
        repo_prefix: str,
        visible_prefix: str,
        *,
        read_only: bool = False,
    ) -> MountedPathBackend:
        return cls(
            context_backend,
            repo_prefix=repo_prefix,
            visible_prefix=visible_prefix,
            read_only=read_only,
        )

    @classmethod
    def file(
        cls, context_backend: BackendProtocol, repo_file: str, visible_file: str
    ) -> MountedPathBackend:
        return cls(
            context_backend,
            repo_file=repo_file,
            visible_file=visible_file,
            read_only=True,
        )

    def ls(self, path: str = "/") -> LsResult:
        return self._remap_path_collections(self._backend.ls(self._repo_path(path)))

    async def als(self, path: str = "/") -> LsResult:
        return self._remap_path_collections(await self._backend.als(self._repo_path(path)))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self._backend.read(self._repo_path(file_path), offset=offset, limit=limit)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return await self._backend.aread(self._repo_path(file_path), offset=offset, limit=limit)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        repo_path = self._repo_path(path) if path is not None else path
        repo_glob = self._repo_glob(glob) if glob else glob
        # Forward the cap only when set, so wrapped backends without the
        # ``max_count`` keyword keep working; ``CompositeBackend`` trims anyway.
        result = (
            self._backend.grep(pattern, repo_path, repo_glob, max_count=max_count)
            if max_count is not None
            else self._backend.grep(pattern, repo_path, repo_glob)
        )
        return self._remap_path_collections(result)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        repo_path = self._repo_path(path) if path is not None else path
        repo_glob = self._repo_glob(glob) if glob else glob
        result = (
            await self._backend.agrep(pattern, repo_path, repo_glob, max_count=max_count)
            if max_count is not None
            else await self._backend.agrep(pattern, repo_path, repo_glob)
        )
        return self._remap_path_collections(result)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self._remap_path_collections(
            self._backend.glob(self._repo_glob(pattern), self._repo_path(path or "/"))
        )

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self._remap_path_collections(
            await self._backend.aglob(self._repo_glob(pattern), self._repo_path(path or "/"))
        )

    def write(self, file_path: str, content: str) -> WriteResult:
        self._assert_writable()
        return self._with_visible_path(
            self._backend.write(self._repo_path(file_path), content),
            file_path,
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        self._assert_writable()
        return self._with_visible_path(
            await self._backend.awrite(self._repo_path(file_path), content),
            file_path,
        )

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        self._assert_writable()
        return self._with_visible_path(
            self._backend.edit(
                self._repo_path(file_path),
                old_string,
                new_string,
                replace_all=replace_all,
            ),
            file_path,
        )

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        self._assert_writable()
        return self._with_visible_path(
            await self._backend.aedit(
                self._repo_path(file_path),
                old_string,
                new_string,
                replace_all=replace_all,
            ),
            file_path,
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        self._assert_writable()
        responses = self._backend.upload_files(
            [(self._repo_path(path), content) for path, content in files]
        )
        return [
            self._with_visible_path(response, files[index][0])
            for index, response in enumerate(responses)
        ]

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        self._assert_writable()
        responses = await self._backend.aupload_files(
            [(self._repo_path(path), content) for path, content in files]
        )
        return [
            self._with_visible_path(response, files[index][0])
            for index, response in enumerate(responses)
        ]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses = self._backend.download_files([self._repo_path(path) for path in paths])
        return [
            self._with_visible_path(response, paths[index])
            for index, response in enumerate(responses)
        ]

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses = await self._backend.adownload_files([self._repo_path(path) for path in paths])
        return [
            self._with_visible_path(response, paths[index])
            for index, response in enumerate(responses)
        ]

    def _repo_path(self, path: str) -> str:
        assert_safe_mounted_path(path)
        if self._repo_file is not None:
            return ensure_absolute(self._repo_file)
        suffix = trim_slashes(path)
        if not self._repo_prefix:
            return ensure_absolute(suffix)
        joined = f"{self._repo_prefix}/{suffix}" if suffix else self._repo_prefix
        return ensure_absolute(joined)

    def _repo_glob(self, pattern: str) -> str:
        assert_safe_mounted_path(pattern)
        if self._repo_file is not None:
            return pattern
        suffix = trim_slashes(pattern)
        if not self._repo_prefix:
            return suffix
        return f"{self._repo_prefix}/{suffix}" if suffix else self._repo_prefix

    def _from_repo_path(self, path: str) -> str:
        normalized = ensure_absolute(path)
        if self._repo_file is not None:
            return "/" if normalized == ensure_absolute(self._repo_file) else normalized
        if not self._repo_prefix:
            return normalized
        prefix = ensure_absolute(self._repo_prefix)
        if normalized == prefix:
            return "/"
        if normalized.startswith(f"{prefix}/"):
            return ensure_absolute(normalized[len(prefix) :])
        return normalized

    def _visible_path(self, path: str) -> str:
        if self._visible_file is not None:
            return self._visible_file
        suffix = trim_slashes(path)
        return f"{self._visible_prefix}/{suffix}" if suffix else self._visible_prefix

    def _remap_path_collections(self, result: _ResultT) -> _ResultT:
        for key in ("entries", "files", "matches"):
            values = result_value(result, key)
            if isinstance(values, list):
                for item in values:
                    set_result_path(item, self._from_repo_path)
        return result

    def _with_visible_path(self, result: _ResultT, backend_path: str) -> _ResultT:
        set_result_path(result, lambda _: self._visible_path(backend_path))
        return result

    def _assert_writable(self) -> None:
        """Deploy-owned mounts (instructions, skills) reject writes."""
        if not self._read_only:
            return
        label = self._visible_file if self._visible_file is not None else self._visible_prefix
        raise ValueError(f"Context Hub mount {label} is read-only")


def managed_context_permissions(
    existing: object,
    filesystem_permission_cls: type[FilesystemPermission],
) -> list[FilesystemPermission]:
    """Path-permission rules for non-sandbox (StateBackend) agents.

    When a sandbox backend is present, callers must pass ``[]`` instead: deepagents
    ``resolve_backend()`` adapts CompositeBackend and drops ``route_prefixes``, so
    any non-empty permissions list disables ``execute``. Deploy-owned paths are
    enforced via read-only :class:`MountedPathBackend` mounts instead.
    """
    managed = filesystem_permission_cls(
        operations=["write"],
        paths=["/skills/**"],
        mode="deny",
    )
    if isinstance(existing, (list, tuple)):
        return [managed, *existing]
    return [managed]
