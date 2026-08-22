import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from blockbuster import BlockBuster
from deepagents.backends import LocalShellBackend
from deepagents.backends.filesystem import FilesystemBackend

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


@contextmanager
def detect_blocking_calls() -> Iterator[None]:
    """Leak-proof ``blockbuster_ctx``: deactivates even when the body raises."""
    blockbuster = BlockBuster()
    blockbuster.activate()
    try:
        yield
    finally:
        blockbuster.deactivate()


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
    assert hosted.startswith(WORKING_ENV_SECTION.format(working_dir="/workspace"))
    assert DESKTOP_PR_SECTION not in hosted

    local = construct_system_prompt(
        working_dir="/workspace", source="desktop", local_workspace=True
    )
    assert local.startswith(DESKTOP_WORKING_ENV_SECTION.format(working_dir="/workspace"))
    assert DESKTOP_PR_SECTION in local


async def test_cloud_runs_keep_scratch_files_in_the_sandbox() -> None:
    assert await CloudRunEnvironment("thread-cloud", {}).scratch_routes("thread-cloud") == {}


async def test_artifact_routes_stay_out_of_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = tmp_path / "artifacts"
    monkeypatch.setenv("OPEN_SWE_LOCAL_ARTIFACTS_DIR", str(artifacts))

    with detect_blocking_calls():
        routes = await DesktopRunEnvironment("thread-1", {}).scratch_routes("thread-1")
    assert set(routes) == {"/large_tool_results/", "/conversation_history/"}
    for prefix, backend in routes.items():
        assert isinstance(backend, FilesystemBackend)
        root = Path(str(backend.cwd)).resolve()
        assert root.is_dir()
        assert root == (artifacts / "thread-1" / prefix.strip("/")).resolve()


async def test_artifact_routes_reject_a_traversing_thread_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = tmp_path / "artifacts"
    monkeypatch.setenv("OPEN_SWE_LOCAL_ARTIFACTS_DIR", str(artifacts))

    routes = await DesktopRunEnvironment("../../etc", {}).scratch_routes("../../etc")
    for backend in routes.values():
        assert isinstance(backend, FilesystemBackend)
        root = Path(str(backend.cwd)).resolve()
        assert artifacts.resolve() in root.parents
