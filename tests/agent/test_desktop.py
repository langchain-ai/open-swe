import json
from pathlib import Path

import pytest

from agent.desktop import create_desktop_backend, resolve_desktop_project


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
