import json
from pathlib import Path

import pytest
from deepagents.backends import LocalShellBackend

from agent.graphs.run_environment import (
    CloudRunEnvironment,
    DesktopRunEnvironment,
    resolve_desktop_project,
)
from agent.prompt import (
    DESKTOP_PR_SECTION,
    DESKTOP_WORKING_ENV_SECTION,
    WORKING_ENV_SECTION,
    construct_system_prompt,
)


async def test_desktop_backend_allows_registered_project_without_provider_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    allowlist = tmp_path / "projects.json"
    allowlist.write_text(json.dumps([{"cwd": str(project)}]))
    monkeypatch.setenv("OPEN_SWE_LOCAL_PROJECTS_FILE", str(allowlist))
    monkeypatch.setenv("PATH", "/bin")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")

    configurable = {"local_project_path": str(project)}
    assert resolve_desktop_project(configurable) == str(project)
    backend = await DesktopRunEnvironment("thread-desktop", configurable).make_backend()
    assert isinstance(backend, LocalShellBackend)
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


def test_local_workspace_flag_is_what_switches_the_prompt_not_the_source() -> None:
    assert CloudRunEnvironment("thread-cloud", {}).local_workspace is False
    assert DesktopRunEnvironment("thread-desktop", {}).local_workspace is True

    hosted = construct_system_prompt(working_dir="/workspace", source="desktop")
    assert hosted.startswith(WORKING_ENV_SECTION)
    assert DESKTOP_PR_SECTION not in hosted

    local = construct_system_prompt(
        working_dir="/workspace", source="desktop", local_workspace=True
    )
    assert local.startswith(DESKTOP_WORKING_ENV_SECTION)
    assert DESKTOP_PR_SECTION in local
