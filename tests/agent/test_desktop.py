import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from blockbuster import BlockBuster
from deepagents.backends.filesystem import FilesystemBackend

from agent.desktop import (
    DesktopCompositeBackend,
    _artifacts_root,
    create_desktop_backend,
    desktop_artifact_routes,
    resolve_desktop_project,
    validate_path,
)
from agent.run_config import RunConfig
from agent.sandboxes.read_only_backend import ReadOnlyBackend


@contextmanager
def detect_blocking_calls() -> Iterator[None]:
    """Leak-proof ``blockbuster_ctx``: deactivates even when the body raises."""
    blockbuster = BlockBuster()
    blockbuster.activate()
    try:
        yield
    finally:
        blockbuster.deactivate()


def test_desktop_backend_allows_a_thread_worktree_without_provider_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktrees = tmp_path / "worktrees"
    project = worktrees / "project-abc12345"
    project.mkdir(parents=True)
    marker = project / "marker.txt"
    marker.write_text("marker")
    monkeypatch.setenv("OPEN_SWE_LOCAL_WORKTREES_DIR", str(worktrees))
    monkeypatch.setenv("PATH", "/bin")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")

    assert resolve_desktop_project(RunConfig(local_project_path=str(project))) == str(project)
    backend = create_desktop_backend(RunConfig(local_project_path=str(project)))
    assert backend._env.get("PATH", "").endswith(os.pathsep + "/bin")
    assert "OPENAI_API_KEY" not in backend._env
    # Real paths, so a path the shell prints is a path the file tools accept.
    assert backend.read(str(marker)).file_data == {"content": "marker", "encoding": "utf-8"}
    assert backend.read("marker.txt").file_data == {"content": "marker", "encoding": "utf-8"}


def test_desktop_backend_puts_the_project_interpreter_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktrees = tmp_path / "worktrees"
    project = worktrees / "project-abc12345"
    scripts = project / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    for name in ("python.exe", "pytest.exe"):
        (scripts / name).write_bytes(b"")
    monkeypatch.setenv("OPEN_SWE_LOCAL_WORKTREES_DIR", str(worktrees))
    monkeypatch.setenv("PATH", "/usr/bin")

    backend = create_desktop_backend(RunConfig(local_project_path=str(project)))

    path_entries = backend._env["PATH"].split(os.pathsep)
    assert Path(path_entries[0]) == scripts
    assert "/usr/bin" in path_entries
    # The interpreter directory is also one the shell may name absolutely.
    assert scripts in backend._extra_roots


def test_desktop_backend_allows_an_allowlisted_project_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    allowlist = tmp_path / "projects.json"
    allowlist.write_text(json.dumps([{"cwd": str(project)}]))
    monkeypatch.setenv("OPEN_SWE_LOCAL_PROJECTS_FILE", str(allowlist))
    monkeypatch.setenv("OPEN_SWE_LOCAL_WORKTREES_DIR", str(tmp_path / "worktrees"))

    assert resolve_desktop_project(RunConfig(local_project_path=str(project))) == str(project)


def test_desktop_backend_rejects_an_unregistered_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    allowlist = tmp_path / "projects.json"
    allowlist.write_text(json.dumps([]))
    monkeypatch.setenv("OPEN_SWE_LOCAL_PROJECTS_FILE", str(allowlist))
    monkeypatch.setenv("OPEN_SWE_LOCAL_WORKTREES_DIR", str(tmp_path / "worktrees"))

    with pytest.raises(ValueError, match="not an allowed project directory"):
        resolve_desktop_project(RunConfig(local_project_path=str(project)))


async def test_artifact_routes_stay_out_of_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = tmp_path / "artifacts"
    monkeypatch.setenv("OPEN_SWE_LOCAL_ARTIFACTS_DIR", str(artifacts))

    with detect_blocking_calls():
        routes = await desktop_artifact_routes("thread-1")
    assert set(routes) == {"/large_tool_results/", "/conversation_history/"}
    for prefix, backend in routes.items():
        root = Path(str(backend.cwd)).resolve()
        assert root.is_dir()
        assert root == (artifacts / "thread-1" / prefix.strip("/")).resolve()


async def test_artifact_routes_reject_a_traversing_thread_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = tmp_path / "artifacts"
    monkeypatch.setenv("OPEN_SWE_LOCAL_ARTIFACTS_DIR", str(artifacts))

    routes = await desktop_artifact_routes("../../etc")
    for backend in routes.values():
        root = Path(str(backend.cwd)).resolve()
        assert artifacts.resolve() in root.parents


def test_artifacts_root_defaults_to_a_per_user_temp_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPEN_SWE_LOCAL_ARTIFACTS_DIR", raising=False)

    root = _artifacts_root()

    assert root.parent == Path(tempfile.gettempdir())
    assert root.name.startswith("open-swe-artifacts-")


def test_desktop_backend_reads_the_file_its_real_path_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    (project / "src" / "pkg").mkdir(parents=True)
    (project / "src" / "pkg" / "__init__.py").write_text("def value():\n    return 1\n")
    (project / "src" / "pkg" / "_native.py").write_text("def helper():\n    return 2\n")
    allowlist = tmp_path / "projects.json"
    allowlist.write_text(json.dumps([{"cwd": str(project)}]))
    monkeypatch.setenv("OPEN_SWE_LOCAL_PROJECTS_FILE", str(allowlist))
    monkeypatch.setenv("OPEN_SWE_LOCAL_WORKTREES_DIR", str(tmp_path / "worktrees"))
    cfg = RunConfig(local_project_path=str(project))

    composite = DesktopCompositeBackend(
        default=create_desktop_backend(cfg),
        routes={
            "/bundled_skills/": ReadOnlyBackend(FilesystemBackend(root_dir=tmp_path / "skills"))
        },
        project_dir=resolve_desktop_project(cfg),
    )

    target = str(project / "src" / "pkg" / "__init__.py")
    assert composite.read(target).file_data is not None
    assert "return 1" in composite.read(target).file_data["content"]
    listing = composite.ls(str(project / "src" / "pkg"))
    assert {Path(str(entry["path"])).name for entry in listing.entries} == {
        "__init__.py",
        "_native.py",
    }
    assert composite.glob("**/*.py", str(project / "src")).matches


def test_desktop_backend_still_routes_scratch_prefixes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "history.md").write_text("earlier turns")
    allowlist = tmp_path / "projects.json"
    allowlist.write_text(json.dumps([{"cwd": str(project)}]))
    monkeypatch.setenv("OPEN_SWE_LOCAL_PROJECTS_FILE", str(allowlist))
    monkeypatch.setenv("OPEN_SWE_LOCAL_WORKTREES_DIR", str(tmp_path / "worktrees"))
    cfg = RunConfig(local_project_path=str(project))

    composite = DesktopCompositeBackend(
        default=create_desktop_backend(cfg),
        routes={"/conversation_history/": FilesystemBackend(root_dir=scratch)},
        project_dir=resolve_desktop_project(cfg),
    )

    read = composite.read("/conversation_history/history.md")
    assert read.file_data == {"content": "earlier turns", "encoding": "utf-8"}


def test_desktop_backend_resolves_a_virtual_path_under_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_text("value = 1\n")
    allowlist = tmp_path / "projects.json"
    allowlist.write_text(json.dumps([{"cwd": str(project)}]))
    monkeypatch.setenv("OPEN_SWE_LOCAL_PROJECTS_FILE", str(allowlist))
    monkeypatch.setenv("OPEN_SWE_LOCAL_WORKTREES_DIR", str(tmp_path / "worktrees"))
    cfg = RunConfig(local_project_path=str(project))

    composite = DesktopCompositeBackend(
        default=create_desktop_backend(cfg),
        routes={},
        project_dir=resolve_desktop_project(cfg),
    )

    # The file tools normalize arguments to virtual paths; "/src/app.py" means
    # the project's src, not the drive root's.
    assert composite.read("/src/app.py").file_data == {
        "content": "value = 1\n",
        "encoding": "utf-8",
    }
    assert composite.read(str(project / "src" / "app.py")).file_data == {
        "content": "value = 1\n",
        "encoding": "utf-8",
    }


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (r"D:\projects\app\src\main.py", r"D:\projects\app\src\main.py"),
        ("C:/projects/app/src/main.py", "C:/projects/app/src/main.py"),
        ("/src/main.py", "/src/main.py"),
        ("src/main.py", "/src/main.py"),
    ],
)
def test_validate_path_keeps_a_windows_path_verbatim(path: str, expected: str) -> None:
    assert validate_path(path) == expected
