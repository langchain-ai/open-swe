"""Composite backend mounts for instructions, skills, and memory views."""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from managed_deepagents._memory.context_io import result_value, set_result_path
from managed_deepagents._memory.paths import (
    assert_safe_mounted_path,
    ensure_absolute,
    strip_trailing_slash,
    trim_slashes,
)

if TYPE_CHECKING:
    from deepagents import FilesystemPermission
    from deepagents.backends import StateBackend

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
        "/instructions.md/": MountedPathBackend.file(
            context_backend, "instructions.md", "/instructions.md"
        ),
        "/skills/": selected_skills_backend
        if selected_skills_backend is not None
        else MountedPathBackend.directory(context_backend, "skills", "/skills", read_only=True),
    }
    # Unmounted layers still get a route so writes fail visibly instead of
    # landing in the ephemeral default backend and reporting success.
    routes["/memories/agent/"] = (
        # Deployment-shared procedural memory on the deploy Hub repo.
        MountedPathBackend.directory(context_backend, "memories/agent", "/memories/agent")
        if mount_agent_memory
        else UnmountedMemoryBackend("agent")
    )
    routes["/memories/user/"] = (
        MountedPathBackend.directory(user_memory_backend, "", "/memories/user")
        if user_memory_backend is not None
        else UnmountedMemoryBackend("user")
    )
    return routes


class UnmountedMemoryBackend(BackendProtocol):
    """Route for a memory layer this run did not mount.

    Reads and writes return tool errors so the model knows nothing was stored.
    Searches return no matches: CompositeBackend fans root searches out to every
    route and would fail the whole search on one route's error.
    """

    def __init__(self, layer: Literal["agent", "user"]) -> None:
        self._layer = layer

    def ls(self, path: str = "/") -> LsResult:
        return LsResult(error=self._error("list", path))

    async def als(self, path: str = "/") -> LsResult:
        return self.ls(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        del offset, limit
        return ReadResult(error=self._error("read", file_path))

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self.read(file_path, offset, limit)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        del pattern, path, glob, max_count
        return GrepResult(matches=[])

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return self.grep(pattern, path, glob, max_count=max_count)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        del pattern, path
        return GlobResult(matches=[])

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self.glob(pattern, path)

    def write(self, file_path: str, content: str) -> WriteResult:
        del content
        return WriteResult(error=self._error("write", file_path))

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return self.write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        del old_string, new_string, replace_all
        return EditResult(error=self._error("edit", file_path))

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return self.edit(file_path, old_string, new_string, replace_all)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self.upload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [FileDownloadResponse(path=path, error="permission_denied") for path in paths]

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self.download_files(paths)

    def _error(self, action: str, path: str) -> str:
        assert_safe_mounted_path(path)
        visible = f"/memories/{self._layer}/{trim_slashes(path)}"
        outcome = " Nothing was saved." if action in {"write", "edit"} else ""
        reason = f"{self._layer} memory is not mounted for this run."
        return f"Cannot {action} {visible}: {reason}{outcome}"


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

    @property
    def visible_file(self) -> str | None:
        return self._visible_file

    def ls(self, path: str = "/") -> LsResult:
        if error := self._missing_under_file(path):
            return LsResult(error=error)
        return self._remap_path_collections(self._backend.ls(self._ls_path(path)))

    async def als(self, path: str = "/") -> LsResult:
        if error := self._missing_under_file(path):
            return LsResult(error=error)
        return self._remap_path_collections(await self._backend.als(self._ls_path(path)))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        if error := self._missing_under_file(file_path):
            return ReadResult(error=error)
        return self._backend.read(self._repo_path(file_path), offset=offset, limit=limit)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        if error := self._missing_under_file(file_path):
            return ReadResult(error=error)
        return await self._backend.aread(self._repo_path(file_path), offset=offset, limit=limit)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        if error := self._missing_under_file(path):
            return GrepResult(error=error)
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
        if error := self._missing_under_file(path):
            return GrepResult(error=error)
        repo_path = self._repo_path(path) if path is not None else path
        repo_glob = self._repo_glob(glob) if glob else glob
        result = (
            await self._backend.agrep(pattern, repo_path, repo_glob, max_count=max_count)
            if max_count is not None
            else await self._backend.agrep(pattern, repo_path, repo_glob)
        )
        return self._remap_path_collections(result)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        if error := self._missing_under_file(path):
            return GlobResult(error=error)
        assert_safe_mounted_path(pattern)
        return self._remap_path_collections(
            self._backend.glob(pattern, self._repo_path(path or "/"))
        )

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        if error := self._missing_under_file(path):
            return GlobResult(error=error)
        assert_safe_mounted_path(pattern)
        return self._remap_path_collections(
            await self._backend.aglob(pattern, self._repo_path(path or "/"))
        )

    def write(self, file_path: str, content: str) -> WriteResult:
        if error := self._missing_under_file(file_path) or self._read_only_error():
            return WriteResult(error=error)
        return self._with_visible_path(
            self._backend.write(self._repo_path(file_path), content),
            file_path,
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        if error := self._missing_under_file(file_path) or self._read_only_error():
            return WriteResult(error=error)
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
        if error := self._missing_under_file(file_path) or self._read_only_error():
            return EditResult(error=error)
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
        if error := self._missing_under_file(file_path) or self._read_only_error():
            return EditResult(error=error)
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
        if self._read_only:
            return self._read_only_uploads(files)
        responses = self._backend.upload_files(
            [(self._repo_path(path), content) for path, content in files]
        )
        return [
            self._with_visible_path(response, files[index][0])
            for index, response in enumerate(responses)
        ]

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        if self._read_only:
            return self._read_only_uploads(files)
        responses = await self._backend.aupload_files(
            [(self._repo_path(path), content) for path, content in files]
        )
        return [
            self._with_visible_path(response, files[index][0])
            for index, response in enumerate(responses)
        ]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        found = [path for path in paths if self._missing_under_file(path) is None]
        responses = self._backend.download_files([self._repo_path(path) for path in found])
        return self._download_responses(paths, found, responses)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        found = [path for path in paths if self._missing_under_file(path) is None]
        responses = await self._backend.adownload_files([self._repo_path(path) for path in found])
        return self._download_responses(paths, found, responses)

    def _download_responses(
        self, paths: list[str], found: list[str], responses: list[FileDownloadResponse]
    ) -> list[FileDownloadResponse]:
        return [
            self._with_visible_path(responses[found.index(path)], path)
            if path in found
            else FileDownloadResponse(path=path, content=None, error="file_not_found")
            for path in paths
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

    def _ls_path(self, path: str) -> str:
        """A file mount lists its parent and keeps only its own file."""
        if self._repo_file is not None:
            return posixpath.dirname(ensure_absolute(self._repo_file))
        return self._repo_path(path)

    def _missing_under_file(self, path: str | None) -> str | None:
        """The tool error for a path under a file mount, which has no children."""
        suffix = trim_slashes(path or "")
        if not suffix or self._visible_file is None:
            return None
        file = self._visible_file
        return f"'{file}/{suffix}' does not exist: {file} is a file, not a directory"

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
                if self._repo_file is not None:
                    repo_file = ensure_absolute(self._repo_file)
                    values[:] = [
                        item
                        for item in values
                        if ensure_absolute(str(result_value(item, "path"))) == repo_file
                    ]
                for item in values:
                    set_result_path(item, self._from_repo_path)
        return result

    def _with_visible_path(self, result: _ResultT, backend_path: str) -> _ResultT:
        set_result_path(result, lambda _: self._visible_path(backend_path))
        return result

    def _read_only_error(self) -> str | None:
        """The tool error for a write to a deploy-owned mount (instructions, skills)."""
        if not self._read_only:
            return None
        label = self._visible_file if self._visible_file is not None else self._visible_prefix
        return f"Context Hub mount {label} is read-only"

    def _read_only_uploads(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [
            FileUploadResponse(path=self._visible_path(path), error="permission_denied")
            for path, _ in files
        ]


class ManagedCompositeBackend(CompositeBackend):
    """Lists file mounts as files.

    deepagents treats every route as a directory: ``ls("/")`` marks it
    ``is_dir``, and paths in routed results keep its trailing ``/``.
    """

    def __init__(
        self, default: BackendProtocol, routes: dict[str, BackendProtocol], **kwargs: Any
    ) -> None:
        super().__init__(default, routes, **kwargs)
        self._file_mounts = {
            backend.visible_file
            for backend in routes.values()
            if isinstance(backend, MountedPathBackend) and backend.visible_file is not None
        }

    def ls(self, path: str) -> LsResult:
        return self._as_file_mounts(super().ls(path))

    async def als(self, path: str) -> LsResult:
        return self._as_file_mounts(await super().als(path))

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self._as_file_mounts(super().glob(pattern, path))

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self._as_file_mounts(await super().aglob(pattern, path))

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return self._as_file_mounts(super().grep(pattern, path, glob, max_count=max_count))

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return self._as_file_mounts(await super().agrep(pattern, path, glob, max_count=max_count))

    def _as_file_mounts(self, result: _ResultT) -> _ResultT:
        for key in ("entries", "matches"):
            values = result_value(result, key)
            for item in values if isinstance(values, list) else []:
                entry = cast("dict[str, object]", item)
                path = strip_trailing_slash(str(entry["path"]))
                if path in self._file_mounts:
                    entry["path"] = path
                    if "is_dir" in entry:
                        entry["is_dir"] = False
        return result


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
