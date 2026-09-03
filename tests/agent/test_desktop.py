import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from blockbuster import BlockBuster

from agent.desktop import (
    _artifacts_root,
    create_desktop_backend,
    desktop_artifact_routes,
    resolve_desktop_project,
)
from agent.run_config import RunConfig


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
    assert backend._env.get("PATH") == "/bin"
    assert "OPENAI_API_KEY" not in backend._env
    assert backend.read(str(marker)).file_data == {"content": "marker", "encoding": "utf-8"}


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


def test_artifacts_root_uses_username_without_posix_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPEN_SWE_LOCAL_ARTIFACTS_DIR", raising=False)
    monkeypatch.delattr("agent.desktop.os.getuid", raising=False)
    monkeypatch.setattr("agent.desktop.getpass.getuser", lambda: "desktop-user")

    assert _artifacts_root().name == "open-swe-artifacts-desktop-user"


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
