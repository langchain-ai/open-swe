"""Unit tests for ``_MountedPathBackend`` path remapping.

The deployed runtime drives the agent asynchronously (LangGraph ``astream``),
so the composite backend routes to ``_MountedPathBackend`` via its ``a*``
methods (``als``, ``aread``, …). These tests pin down that the async surface
exists and mirrors the sync remapping — a missing async method regressed a
deployment with ``AttributeError: '_MountedPathBackend' object has no
attribute 'als'``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend

from managed_deepagents._memory.mounts import (
    MountedPathBackend as _MountedPathBackend,
)
from managed_deepagents._memory.mounts import (
    managed_context_backend as _managed_context_backend,
)


class _FakeBackend:
    """Records the repo-relative paths each method was called with.

    Every method has both a sync and an ``a``-prefixed async variant, matching
    the ``deepagents`` ``BackendProtocol`` surface that real backends inherit.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def ls(self, path: str) -> Any:
        self.calls.append(("ls", (path,)))
        return SimpleNamespace(entries=[SimpleNamespace(path=f"{path.rstrip('/')}/note.md")])

    async def als(self, path: str) -> Any:
        self.calls.append(("als", (path,)))
        return SimpleNamespace(entries=[SimpleNamespace(path=f"{path.rstrip('/')}/note.md")])

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> Any:
        self.calls.append(("read", (file_path, offset, limit)))
        return SimpleNamespace(content="sync")

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> Any:
        self.calls.append(("aread", (file_path, offset, limit)))
        return SimpleNamespace(content="async")

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> Any:
        self.calls.append(("grep", (pattern, path, glob)))
        return SimpleNamespace(matches=[SimpleNamespace(path=f"{(path or '').rstrip('/')}/hit.md")])

    async def agrep(self, pattern: str, path: str | None = None, glob: str | None = None) -> Any:
        self.calls.append(("agrep", (pattern, path, glob)))
        return SimpleNamespace(matches=[SimpleNamespace(path=f"{(path or '').rstrip('/')}/hit.md")])

    def glob(self, pattern: str, path: str | None = None) -> Any:
        self.calls.append(("glob", (pattern, path)))
        return SimpleNamespace(files=[SimpleNamespace(path=f"{(path or '').rstrip('/')}/found.md")])

    async def aglob(self, pattern: str, path: str | None = None) -> Any:
        self.calls.append(("aglob", (pattern, path)))
        return SimpleNamespace(files=[SimpleNamespace(path=f"{(path or '').rstrip('/')}/found.md")])

    def write(self, file_path: str, content: str) -> Any:
        self.calls.append(("write", (file_path, content)))
        return SimpleNamespace(path=file_path)

    async def awrite(self, file_path: str, content: str) -> Any:
        self.calls.append(("awrite", (file_path, content)))
        return SimpleNamespace(path=file_path)

    def edit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> Any:
        self.calls.append(("edit", (file_path, old_string, new_string, replace_all)))
        return SimpleNamespace(path=file_path)

    async def aedit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> Any:
        self.calls.append(("aedit", (file_path, old_string, new_string, replace_all)))
        return SimpleNamespace(path=file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[Any]:
        self.calls.append(("upload_files", (tuple(files),)))
        return [SimpleNamespace(path=path) for path, _ in files]

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[Any]:
        self.calls.append(("aupload_files", (tuple(files),)))
        return [SimpleNamespace(path=path) for path, _ in files]

    def download_files(self, paths: list[str]) -> list[Any]:
        self.calls.append(("download_files", (tuple(paths),)))
        return [SimpleNamespace(path=path) for path in paths]

    async def adownload_files(self, paths: list[str]) -> list[Any]:
        self.calls.append(("adownload_files", (tuple(paths),)))
        return [SimpleNamespace(path=path) for path in paths]


class _FakeStateBackend:
    def __init__(self, runtime: object | None = None) -> None:
        self.runtime = runtime


class _FakeCompositeBackend:
    def __init__(self, *, default: Any, routes: dict[str, Any]) -> None:
        self.default = default
        self.routes = routes


def _directory_backend(backend: _FakeBackend) -> _MountedPathBackend:
    return _MountedPathBackend.directory(backend, "skills", "/skills")


def test_managed_context_backend_returns_instance_with_modern_state_backend() -> None:
    backend = _managed_context_backend(
        _FakeBackend(),
        None,
        _FakeCompositeBackend,
        _FakeStateBackend,
        mount_agent_memory=True,
    )

    assert isinstance(backend, _FakeCompositeBackend)
    assert isinstance(backend.default, _FakeStateBackend)
    assert backend.default.runtime is None
    assert set(backend.routes) == {
        "/instructions.md",
        "/skills/",
        "/memories/agent/",
    }


def test_als_delegates_to_backend_async_ls_and_remaps_paths() -> None:
    backend = _FakeBackend()
    view = _directory_backend(backend)

    result = asyncio.run(view.als("/"))

    assert backend.calls == [("als", ("/skills",))]
    # Entries are returned relative to the mount root; the CompositeBackend
    # re-prepends the "/skills" route prefix, so the repo path "/skills/note.md"
    # is stripped back to "/note.md" here.
    assert result.entries[0].path == "/note.md"


def test_aread_forwards_offset_and_limit_to_repo_path() -> None:
    backend = _FakeBackend()
    view = _directory_backend(backend)

    result = asyncio.run(view.aread("/foo.md", offset=5, limit=10))

    assert backend.calls == [("aread", ("/skills/foo.md", 5, 10))]
    assert result.content == "async"


def test_agrep_remaps_pattern_path_and_results() -> None:
    backend = _FakeBackend()
    view = _directory_backend(backend)

    result = asyncio.run(view.agrep("TODO", path="/", glob="*.md"))

    assert backend.calls[0][0] == "agrep"
    _, (pattern, path, glob) = backend.calls[0]
    assert pattern == "TODO"
    assert path == "/skills"
    assert glob == "skills/*.md"
    assert result.matches[0].path == "/hit.md"


def test_aglob_uses_repo_glob_and_repo_path() -> None:
    backend = _FakeBackend()
    view = _directory_backend(backend)

    result = asyncio.run(view.aglob("*.md"))

    assert backend.calls[0][0] == "aglob"
    _, (pattern, path) = backend.calls[0]
    assert pattern == "skills/*.md"
    assert path == "/skills"
    assert result.files[0].path == "/found.md"


def test_awrite_and_aedit_report_visible_paths() -> None:
    backend = _FakeBackend()
    view = _directory_backend(backend)

    write_result = asyncio.run(view.awrite("/note.md", "content"))
    edit_result = asyncio.run(view.aedit("/note.md", "a", "b", replace_all=True))

    assert ("awrite", ("/skills/note.md", "content")) in backend.calls
    assert ("aedit", ("/skills/note.md", "a", "b", True)) in backend.calls
    # Results are reported under the visible mount, not the repo path.
    assert write_result.path == "/skills/note.md"
    assert edit_result.path == "/skills/note.md"


def test_aupload_and_adownload_roundtrip_visible_paths() -> None:
    backend = _FakeBackend()
    view = _directory_backend(backend)

    uploaded = asyncio.run(view.aupload_files([("/a.md", b"x")]))
    downloaded = asyncio.run(view.adownload_files(["/a.md"]))

    upload_call = next(c for c in backend.calls if c[0] == "aupload_files")
    download_call = next(c for c in backend.calls if c[0] == "adownload_files")
    # The backend is called with repo-relative paths ...
    assert upload_call[1][0] == (("/skills/a.md", b"x"),)
    assert download_call[1][0] == ("/skills/a.md",)
    # ... while the responses are reported under the visible mount.
    assert uploaded[0].path == "/skills/a.md"
    assert downloaded[0].path == "/skills/a.md"


def test_file_view_async_read_targets_single_repo_file() -> None:
    backend = _FakeBackend()
    view = _MountedPathBackend.file(backend, "instructions.md", "/instructions.md")

    asyncio.run(view.aread("/anything.md"))

    # A file view always resolves to its single repo file regardless of the arg.
    assert backend.calls == [("aread", ("/instructions.md", 0, 2000))]


def test_file_view_rejects_writes() -> None:
    backend = _FakeBackend()
    view = _MountedPathBackend.file(backend, "instructions.md", "/instructions.md")

    with pytest.raises(ValueError, match="read-only"):
        view.write("/", "nope")
    with pytest.raises(ValueError, match="read-only"):
        asyncio.run(view.awrite("/", "nope"))
    with pytest.raises(ValueError, match="read-only"):
        view.edit("/", "a", "b")
    with pytest.raises(ValueError, match="read-only"):
        asyncio.run(view.aupload_files([("/", b"x")]))

    assert backend.calls == []


def test_directory_view_rejects_path_traversal() -> None:
    backend = _FakeBackend()
    view = _directory_backend(backend)

    with pytest.raises(ValueError, match="must not contain traversal segments"):
        view.read("/../secret.md")

    assert backend.calls == []


def test_skills_route_is_read_only_while_memory_stays_writable() -> None:
    backend = _FakeBackend()
    routes = _managed_context_backend(
        backend,
        None,
        _FakeCompositeBackend,
        _FakeStateBackend,
        mount_agent_memory=True,
    ).routes

    with pytest.raises(ValueError, match="read-only"):
        routes["/skills/"].write("/note.md", "x")
    # Agent memory stays writable — that is the whole point of the mount.
    routes["/memories/agent/"].write("/AGENTS.md", "agent")
    assert ("write", ("/memories/agent/AGENTS.md", "agent")) in backend.calls


@pytest.mark.parametrize(
    "path",
    ["/memories/agent/AGENTS.md", "/memories/user/AGENTS.md", "/instructions.md", "/skills/a.md"],
)
def test_composite_delete_on_context_mounts_reports_unsupported(tmp_path: Path, path: str) -> None:
    # Regression: mounts lacked ``delete``, so ``CompositeBackend.delete`` (which
    # only converts ``NotImplementedError``) raised ``AttributeError``. Mounts now
    # inherit the native default and never delete Context Hub content.
    hub = tmp_path / "hub"
    users = tmp_path / "user"
    for root, relative in (
        (hub, "memories/agent/AGENTS.md"),
        (hub, "instructions.md"),
        (hub, "skills/a.md"),
        (users, "AGENTS.md"),
    ):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text("keep")
    composite = _managed_context_backend(
        FilesystemBackend(root_dir=hub, virtual_mode=True),
        None,
        CompositeBackend,
        StateBackend,
        mount_agent_memory=True,
        user_memory_backend=FilesystemBackend(root_dir=users, virtual_mode=True),
    )

    result = composite.delete(path)
    async_result = asyncio.run(composite.adelete(path))

    assert result.error == f"Error: deletion is not supported for '{path}'."
    assert async_result.error == result.error
    assert sorted(p.read_text() for p in tmp_path.rglob("*.md")) == ["keep"] * 4
