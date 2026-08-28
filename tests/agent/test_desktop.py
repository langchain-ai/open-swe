import getpass
import json
import os
import tempfile
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


@contextmanager
def detect_blocking_calls() -> Iterator[None]:
    """Leak-proof ``blockbuster_ctx``: deactivates even when the body raises."""
    blockbuster = BlockBuster()
    blockbuster.activate()
    try:
        yield
    finally:
        blockbuster.deactivate()


def test_desktop_backend_allows_registered_project_without_provider_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    marker = project / "marker.txt"
    marker.write_text("marker")
    allowlist = tmp_path / "projects.json"
    allowlist.write_text(json.dumps([{"cwd": str(project)}]))
    monkeypatch.setenv("OPEN_SWE_LOCAL_PROJECTS_FILE", str(allowlist))
    monkeypatch.setenv("PATH", "/bin")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")

    assert resolve_desktop_project({"local_project_path": str(project)}) == str(project)
    backend = create_desktop_backend({"local_project_path": str(project)})
    assert backend._env.get("PATH") == "/bin"
    assert "OPENAI_API_KEY" not in backend._env
    assert backend.read(str(marker)).file_data == {"content": "marker", "encoding": "utf-8"}


def test_desktop_backend_rejects_unregistered_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    allowlist = tmp_path / "projects.json"
    allowlist.write_text("[]")
    monkeypatch.setenv("OPEN_SWE_LOCAL_PROJECTS_FILE", str(allowlist))

    with pytest.raises(ValueError, match="not an allowed project"):
        resolve_desktop_project({"local_project_path": str(project)})


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


@pytest.mark.parametrize("username", ["ada", "Ada Lovelace", "../../etc", "DOMAIN\ada"])
def test_artifacts_root_falls_back_to_the_login_name_without_getuid(
    username: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``os.getuid`` is POSIX-only, so the desktop backend must not depend on it.

    Exercised on every platform: without this the fallback is only ever reached
    on Windows, which CI does not run.
    """
    monkeypatch.delenv("OPEN_SWE_LOCAL_ARTIFACTS_DIR", raising=False)
    monkeypatch.delattr(os, "getuid", raising=False)
    monkeypatch.setattr(getpass, "getuser", lambda: username)

    root = _artifacts_root()

    # One path segment directly under the temp dir — a hostile USERNAME must not
    # escape it, since getpass.getuser() reads the environment.
    assert root.parent == Path(tempfile.gettempdir())
    assert root.name.startswith("open-swe-artifacts-")
