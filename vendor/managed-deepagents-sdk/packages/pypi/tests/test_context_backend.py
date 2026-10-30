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
from deepagents.backends.context_hub import ContextHubBackend
from langsmith.schemas import FileEntry

from managed_deepagents._memory.mounts import (
    ManagedCompositeBackend as _ManagedCompositeBackend,
)
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
        "/instructions.md/",
        "/skills/",
        "/memories/agent/",
        "/memories/user/",
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


def test_aglob_passes_pattern_with_repo_path() -> None:
    backend = _FakeBackend()
    view = _directory_backend(backend)

    result = asyncio.run(view.aglob("*.md"))

    assert backend.calls[0][0] == "aglob"
    _, (pattern, path) = backend.calls[0]
    assert pattern == "*.md"
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

    asyncio.run(view.aread("/"))
    missing = asyncio.run(view.aread("/anything.md"))

    # A file view reads its single repo file and has no children.
    assert backend.calls == [("aread", ("/instructions.md", 0, 2000))]
    assert missing.error == (
        "'/instructions.md/anything.md' does not exist: /instructions.md is a file, not a directory"
    )


def test_file_view_rejects_writes() -> None:
    backend = _FakeBackend()
    view = _MountedPathBackend.file(backend, "instructions.md", "/instructions.md")

    error = "Context Hub mount /instructions.md is read-only"
    assert view.write("/", "nope").error == error
    assert asyncio.run(view.awrite("/", "nope")).error == error
    assert view.edit("/", "a", "b").error == error
    assert asyncio.run(view.aedit("/", "a", "b")).error == error
    uploaded = view.upload_files([("/", b"x")])
    assert [(item.path, item.error) for item in uploaded] == [
        ("/instructions.md", "permission_denied")
    ]
    uploaded = asyncio.run(view.aupload_files([("/", b"x")]))
    assert [(item.path, item.error) for item in uploaded] == [
        ("/instructions.md", "permission_denied")
    ]

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

    assert (
        routes["/skills/"].write("/note.md", "x").error == "Context Hub mount /skills is read-only"
    )
    uploaded = routes["/skills/"].upload_files([("/note.md", b"x")])
    assert [(item.path, item.error) for item in uploaded] == [
        ("/skills/note.md", "permission_denied")
    ]
    assert backend.calls == []
    # Agent memory stays writable — that is the whole point of the mount.
    routes["/memories/agent/"].write("/AGENTS.md", "agent")
    assert ("write", ("/memories/agent/AGENTS.md", "agent")) in backend.calls


def _delete_fixture(tmp_path: Path, *, mount_user: bool = True) -> CompositeBackend:
    hub = tmp_path / "hub"
    users = tmp_path / "user"
    for root, relative in (
        (hub, "memories/agent/old.md"),
        (hub, "instructions.md"),
        (hub, "skills/a.md"),
        (users, "old.md"),
    ):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text("keep")
    return _managed_context_backend(
        FilesystemBackend(root_dir=hub, virtual_mode=True),
        None,
        CompositeBackend,
        StateBackend,
        mount_agent_memory=True,
        user_memory_backend=FilesystemBackend(root_dir=users, virtual_mode=True)
        if mount_user
        else None,
    )


@pytest.mark.parametrize(
    ("path", "repo_file"),
    [
        ("/memories/agent/old.md", "hub/memories/agent/old.md"),
        ("/memories/user/old.md", "user/old.md"),
    ],
)
@pytest.mark.parametrize("use_async", [False, True])
def test_composite_delete_removes_memory_files(
    tmp_path: Path, path: str, repo_file: str, use_async: bool
) -> None:
    composite = _delete_fixture(tmp_path)

    result = _call(composite, "delete", path, use_async=use_async)

    assert (result.path, result.error) == (path, None)
    assert not (tmp_path / repo_file).exists()


@pytest.mark.parametrize(
    ("path", "error"),
    [
        ("/instructions.md", "Context Hub mount /instructions.md is read-only"),
        ("/skills/a.md", "Context Hub mount /skills is read-only"),
        (
            "/memories/agent/",
            "Cannot delete /memories/agent: delete the files inside it instead",
        ),
    ],
)
def test_composite_delete_keeps_deploy_files_and_mount_roots(
    tmp_path: Path, path: str, error: str
) -> None:
    composite = _delete_fixture(tmp_path)

    assert composite.delete(path).error == error
    assert sorted(p.read_text() for p in tmp_path.rglob("*.md")) == ["keep"] * 4


def test_composite_delete_on_unmounted_user_memory_reports_error(tmp_path: Path) -> None:
    composite = _delete_fixture(tmp_path, mount_user=False)

    assert composite.delete("/memories/user/old.md").error == (
        "Cannot delete /memories/user/old.md: user memory is not mounted for this run."
    )


#: The deploy Hub repo as ``mda deploy`` leaves it.
_HUB_FILES = {
    "instructions.md": "Be helpful.\n",
    ".mda/deploy-manifest.json": '{"instructions":null,"skills":null}\n',
    "skills/research/SKILL.md": "# Research\n",
}
_UNDER_FILE_MOUNT = ["/instructions.md/.mda/", "/instructions.md/.mda/deploy-manifest.json"]


def _deployed_backend(tmp_path: Path) -> CompositeBackend:
    snapshot = SimpleNamespace(
        files={
            path: FileEntry(type="file", content=content) for path, content in _HUB_FILES.items()
        },
        commit_hash="abc123",
    )
    hub = ContextHubBackend("-/my-agent", client=SimpleNamespace(pull_agent=lambda _: snapshot))
    return _managed_context_backend(
        hub,
        FilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        _ManagedCompositeBackend,
        StateBackend,
    )


def _call(backend: object, method: str, *args: Any, use_async: bool) -> Any:
    if use_async:
        return asyncio.run(getattr(backend, f"a{method}")(*args))
    return getattr(backend, method)(*args)


@pytest.mark.parametrize("use_async", [False, True])
def test_file_mount_lists_as_a_file_at_the_root(tmp_path: Path, use_async: bool) -> None:
    result = _call(_deployed_backend(tmp_path), "ls", "/", use_async=use_async)

    assert [(entry["path"], entry["is_dir"]) for entry in result.entries] == [
        ("/instructions.md", False),
        ("/memories/agent/", True),
        ("/memories/user/", True),
        ("/skills/", True),
    ]


@pytest.mark.parametrize("use_async", [False, True])
def test_file_mount_ls_lists_the_file_itself(tmp_path: Path, use_async: bool) -> None:
    result = _call(_deployed_backend(tmp_path), "ls", "/instructions.md", use_async=use_async)

    assert [(entry["path"], entry["is_dir"]) for entry in result.entries] == [
        ("/instructions.md", False)
    ]


@pytest.mark.parametrize("use_async", [False, True])
def test_file_mount_keeps_the_hub_repo_out_of_a_root_glob(tmp_path: Path, use_async: bool) -> None:
    result = _call(_deployed_backend(tmp_path), "glob", "**/*", "/", use_async=use_async)

    assert [entry["path"] for entry in result.matches] == [
        "/instructions.md",
        "/skills/research/SKILL.md",
    ]


@pytest.mark.parametrize("use_async", [False, True])
def test_file_mount_keeps_the_hub_repo_out_of_a_root_grep(tmp_path: Path, use_async: bool) -> None:
    backend = _deployed_backend(tmp_path)

    manifest = _call(backend, "grep", "skills", "/", use_async=use_async)
    instructions = _call(backend, "grep", "helpful", "/", use_async=use_async)

    assert manifest.matches == []
    assert instructions.matches == [{"path": "/instructions.md", "line": 1, "text": "Be helpful."}]


@pytest.mark.parametrize("use_async", [False, True])
def test_file_mount_reads_the_mounted_file(tmp_path: Path, use_async: bool) -> None:
    result = _call(_deployed_backend(tmp_path), "read", "/instructions.md", use_async=use_async)

    assert result.error is None
    assert result.file_data["content"] == "Be helpful.\n"


@pytest.mark.parametrize("use_async", [False, True])
@pytest.mark.parametrize("path", _UNDER_FILE_MOUNT)
def test_file_mount_returns_a_tool_error_from_every_file_tool(
    tmp_path: Path, path: str, use_async: bool
) -> None:
    backend = _deployed_backend(tmp_path)
    not_found = f"'{path.rstrip('/')}' does not exist: /instructions.md is a file, not a directory"

    assert _call(backend, "ls", path, use_async=use_async).error == not_found
    assert _call(backend, "read", path, use_async=use_async).error == not_found
    assert _call(backend, "glob", "**/*", path, use_async=use_async).error == not_found
    assert _call(backend, "grep", "skills", path, use_async=use_async).error == not_found
    assert _call(backend, "write", path, "x", use_async=use_async).error == not_found
    assert _call(backend, "edit", path, "Be", "Stay", use_async=use_async).error == not_found
    [download] = _call(backend, "download_files", [path], use_async=use_async)
    assert (download.path, download.content, download.error) == (path, None, "file_not_found")


@pytest.mark.parametrize("use_async", [False, True])
def test_directory_mount_globs_a_local_filesystem_backend(tmp_path: Path, use_async: bool) -> None:
    hub = tmp_path / "hub"
    (hub / "skills" / "research").mkdir(parents=True)
    (hub / "skills" / "research" / "SKILL.md").write_text("# Research\n")
    (tmp_path / "scratch").mkdir()
    backend = _managed_context_backend(
        FilesystemBackend(root_dir=hub, virtual_mode=True),
        FilesystemBackend(root_dir=tmp_path / "scratch", virtual_mode=True),
        _ManagedCompositeBackend,
        StateBackend,
    )

    result = _call(backend, "glob", "**/SKILL.md", "/skills", use_async=use_async)

    assert [entry["path"] for entry in result.matches] == ["/skills/research/SKILL.md"]
