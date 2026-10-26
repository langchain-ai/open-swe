import asyncio
import json
import os
import re
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import validate_path as _virtual_validate_path
from deepagents.middleware import filesystem as _filesystem_middleware

from agent.config import ENV
from agent.run_config import RunConfig
from agent.sandboxes.local_execution import AsyncLocalShellBackend

SHELL_ENV_KEYS = (
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "SHELL",
    "TMPDIR",
    "SystemRoot",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "USERPROFILE",
)


def is_desktop_run(cfg: RunConfig) -> bool:
    return cfg.source == "desktop"


def _allowed_projects() -> set[str]:
    allowlist_path = ENV.OPEN_SWE_LOCAL_PROJECTS_FILE.optional()
    if not allowlist_path:
        return set()
    with open(allowlist_path, encoding="utf-8") as file:
        entries = json.load(file)
    if not isinstance(entries, list):
        raise ValueError("OPEN_SWE_LOCAL_PROJECTS_FILE must contain a JSON array")
    return {
        os.path.realpath(entry["cwd"] if isinstance(entry, dict) else entry)
        for entry in entries
        if isinstance(entry, str) or (isinstance(entry, dict) and isinstance(entry.get("cwd"), str))
    }


def is_desktop_worktree(path: str) -> bool:
    """Whether the path is a worktree the desktop app created for a thread."""
    worktrees_dir = ENV.OPEN_SWE_LOCAL_WORKTREES_DIR.optional()
    if not worktrees_dir:
        return False
    return Path(os.path.realpath(worktrees_dir)) in Path(os.path.realpath(path)).parents


def resolve_desktop_project(cfg: RunConfig) -> str:
    """The directory a desktop run may work in.

    A thread runs either in a project the user registered in the desktop app or
    in its own git worktree, which the app checks out under
    `OPEN_SWE_LOCAL_WORKTREES_DIR`. Both are named by the same config value, so
    a path is trustworthy when it is allowlisted or contained in that directory.
    """
    requested = cfg.local_project_path
    if not requested:
        raise ValueError("Desktop runs require a local project path")
    project = Path(os.path.realpath(requested))
    if not project.is_dir() or not (
        is_desktop_worktree(requested) or str(project) in _allowed_projects()
    ):
        raise ValueError("local_project_path is not an allowed project directory")
    return str(project)


def create_desktop_backend(cfg: RunConfig) -> AsyncLocalShellBackend:
    # Real paths, so file tools and shell commands name the same directory and
    # a model can hand a path it read back to `execute`. Virtual paths only
    # rewrite file tools; the shell still starts in the real project, which
    # left the model comparing two different views of one working tree.
    return AsyncLocalShellBackend(
        root_dir=resolve_desktop_project(cfg),
        virtual_mode=False,
        extra_roots=_toolchain_roots(),
        env={key: value for key in SHELL_ENV_KEYS if (value := os.environ.get(key))},
    )


def _toolchain_roots() -> list[str]:
    """Absolute locations a desktop command may name besides the project.

    A build needs its interpreter and its shell by absolute path. Those live
    outside the project, so they are named explicitly rather than by widening
    the project root.
    """
    roots = [
        os.path.dirname(sys.executable),
        os.path.dirname(os.path.realpath(sys.executable)),
        os.environ.get("SystemRoot", ""),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    return [root for root in roots if root and os.path.isdir(root)]


_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


def validate_path(path: str, *, allowed_prefixes: Sequence[str] | None = None) -> str:
    """`deepagents`' virtual-path validation that also accepts Windows paths.

    A real absolute path is left exactly as the caller wrote it: backslashes are
    meaningful to `os.path` and a leading `/` would turn `D:\\proj` into a
    virtual root path.
    """
    if _WINDOWS_ABSOLUTE.match(path):
        return path
    return _virtual_validate_path(path, allowed_prefixes=allowed_prefixes)


def install_real_path_file_tools() -> None:
    """Let the file tools accept the real Windows paths a desktop run works in.

    Deep Agents validates every file-tool argument with `validate_path`, which
    normalizes to a virtual POSIX path and rejects `D:\\...` outright. That
    rejection happens in the middleware, before the backend sees the argument,
    so a desktop backend that speaks real paths never gets the chance to
    resolve them. The shell already runs on real paths, which left the model
    reading through one path scheme and running commands in another.

    Windows absolute paths therefore pass through untouched as the backend's own
    real paths. Everything else keeps the virtual normalization, and
    `DesktopCompositeBackend` resolves those virtual paths under the project, so
    both forms name the same file.
    """
    # The tools call the module-level name, so replacing it is what reaches them.
    # The checker rejects any write to a re-exported library attribute, even one
    # whose signature matches; there is no other seam, since the tool closures
    # bind the global rather than a method on the middleware.
    _filesystem_middleware.validate_path = validate_path  # ty: ignore[invalid-assignment]


class DesktopCompositeBackend(CompositeBackend):
    """Composite over the project plus the agent's scratch routes.

    ``CompositeBackend`` routes by leading path segment and returns the key it
    sent to that backend, falling back to the default backend when no prefix
    matches. A desktop run's default backend speaks real Windows paths, so a
    prefix-less ``read_file`` such as ``/agent/...`` reached it as a virtual
    path and failed, and an ``ls`` route could return the previous tool's output.
    Here the project's real paths pass through untouched, whatever the routes:
    each routed call gets the argument the caller already named.
    """

    def __init__(
        self,
        *,
        default: BackendProtocol,
        routes: dict[str, BackendProtocol],
        project_dir: str,
    ) -> None:
        super().__init__(default=default, routes=routes)
        self._project_dir = os.path.normcase(os.path.abspath(project_dir))

    def _in_project(self, path: str) -> bool:
        normalized = os.path.normcase(os.path.abspath(path))
        return normalized == self._project_dir or normalized.startswith(self._project_dir + os.sep)

    def _project_path(self, virtual: str) -> str:
        """Resolve a virtual path under the project instead of the drive root.

        The file tools normalize every argument to a virtual path before the
        backend sees it, and a non-virtual backend resolves a leading "/"
        against the current drive. `/eval-abc/src` would then mean
        `D:\\eval-abc\\src` rather than the project's `eval-abc`. A virtual path
        is relative to the project, which is also where the shell starts.
        """
        return os.path.join(self._project_dir, virtual.lstrip("/"))

    def _route(self, path: str) -> tuple[BackendProtocol, str, str | None]:
        if self._in_project(path):
            return self.default, path, None
        for prefix, backend in self.sorted_routes:
            bare = prefix.rstrip("/")
            if path == bare:
                return backend, "/", prefix
            if path.startswith(f"{bare}/"):
                return backend, path[len(bare) :], prefix
        if path.startswith(("/", "\\")) and not _WINDOWS_ABSOLUTE.match(path):
            return self.default, self._project_path(path), None
        return self.default, path, None

    # Every file operation routes through _route and hands the backend the
    # caller's own argument, so a project file keeps the real path the shell
    # uses and a routed file is addressed by the path the model already knows.
    def _get_backend_and_key(self, key: str) -> tuple[BackendProtocol, str]:
        backend, routed, _prefix = self._route(key)
        return backend, routed

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        backend, path = self._get_backend_and_key(file_path)
        return backend.read(path, offset=offset, limit=limit)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        backend, path = self._get_backend_and_key(file_path)
        return await backend.aread(path, offset=offset, limit=limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        backend, path = self._get_backend_and_key(file_path)
        return backend.write(path, content)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        backend, path = self._get_backend_and_key(file_path)
        return await backend.awrite(path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        backend, path = self._get_backend_and_key(file_path)
        return backend.edit(path, old_string, new_string, replace_all=replace_all)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        backend, path = self._get_backend_and_key(file_path)
        return await backend.aedit(path, old_string, new_string, replace_all=replace_all)

    def ls(self, path: str) -> LsResult:
        backend, routed = self._get_backend_and_key(path)
        return self._coerce_ls_result(backend.ls(routed))

    async def als(self, path: str) -> LsResult:
        backend, routed = self._get_backend_and_key(path)
        return self._coerce_ls_result(await backend.als(routed))

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        backend, routed_pattern, routed_path = self._route_glob(pattern, path)
        return backend.glob(routed_pattern, routed_path)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        backend, routed_pattern, routed_path = self._route_glob(pattern, path)
        return await backend.aglob(routed_pattern, routed_path)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        backend, routed = self._route_optional(path)
        return backend.grep(pattern, routed, glob, max_count=max_count)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        backend, routed = self._route_optional(path)
        return await backend.agrep(pattern, routed, glob, max_count=max_count)

    def _route_optional(self, path: str | None) -> tuple[BackendProtocol, str | None]:
        if path is None:
            return self.default, None
        return self._get_backend_and_key(path)

    def _route_glob(
        self, pattern: str, path: str | None
    ) -> tuple[BackendProtocol, str, str | None]:
        # Without a path the pattern is resolved from the project root, which
        # is a real directory: leave the pattern alone.
        if path is None:
            return self.default, pattern, None
        backend, routed_path = self._get_backend_and_key(path)
        return backend, pattern, routed_path


def _artifacts_root() -> Path:
    configured = ENV.OPEN_SWE_LOCAL_ARTIFACTS_DIR.optional()
    if configured:
        return Path(configured)
    # os.getuid is POSIX-only; Windows desktop builds must not crash on it.
    user = str(os.getuid()) if hasattr(os, "getuid") else os.environ.get("USERNAME", "user")
    return Path(tempfile.gettempdir()) / f"open-swe-artifacts-{user}"


async def desktop_artifact_routes(thread_id: str) -> dict[str, FilesystemBackend]:
    """Backends for the agent's own scratch files on a desktop run.

    Offloaded tool results and evicted history default to the artifacts root,
    which for a desktop run is the user's project: the dumps would show up as
    changes and be swept into the next `git add -A`. Route them out of the
    repository while leaving the virtual paths the model sees unchanged.
    """
    # The thread id becomes a path segment, so it may only be a plain name.
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "-", thread_id or "thread").lstrip(".") or "thread"
    root = _artifacts_root() / safe_id
    routes = {}
    for name in ("large_tool_results", "conversation_history"):
        directory = root / name
        await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
        routes[f"/{name}/"] = await asyncio.to_thread(
            FilesystemBackend, root_dir=directory, virtual_mode=True
        )
    return routes
