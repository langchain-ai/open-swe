import json
from pathlib import Path

import pytest
from blockbuster import blockbuster_ctx

from agent.desktop import (
    create_desktop_backend,
    desktop_artifact_routes,
    resolve_desktop_project,
)


def test_desktop_backend_allows_registered_project_without_provider_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    allowlist = tmp_path / "projects.json"
    allowlist.write_text(json.dumps([{"cwd": str(project)}]))
    monkeypatch.setenv("OPEN_SWE_LOCAL_PROJECTS_FILE", str(allowlist))
    monkeypatch.setenv("PATH", "/bin")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")

    assert resolve_desktop_project({"local_project_path": str(project)}) == str(project)
    backend = create_desktop_backend({"local_project_path": str(project)})
    assert backend._env.get("PATH") == "/bin"
    assert "OPENAI_API_KEY" not in backend._env


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

    with blockbuster_ctx():
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
