from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.graph.state import RunnableConfig

from agent import server
from agent.prompt import construct_sender_context, construct_system_prompt
from agent.tools import environments as env_tools

_READY = {"slug": "base", "name": "Base", "snapshot_status": "ready", "snapshot_id": "env-snap"}


def _config(**configurable: object) -> RunnableConfig:
    return cast(RunnableConfig, {"configurable": configurable})


# --- snapshot precedence ---


@pytest.mark.asyncio
async def test_default_environment_snapshot_wins_over_repo_and_base() -> None:
    with (
        patch.object(server, "resolve_environment", new_callable=AsyncMock, return_value=_READY),
        patch.object(
            server, "resolve_repo_snapshot_id", new_callable=AsyncMock, return_value="repo-snap"
        ),
        patch.object(
            server, "get_admin_base_snapshot_id", new_callable=AsyncMock, return_value="admin-snap"
        ),
    ):
        assert await server._resolve_snapshot_id({"owner": "acme", "name": "repo"}) == "env-snap"


@pytest.mark.asyncio
async def test_environment_without_ready_snapshot_falls_back_to_repo() -> None:
    capturing = {**_READY, "snapshot_status": "capturing"}
    with (
        patch.object(server, "resolve_environment", new_callable=AsyncMock, return_value=capturing),
        patch.object(
            server, "resolve_repo_snapshot_id", new_callable=AsyncMock, return_value="repo-snap"
        ),
        patch.object(
            server, "get_admin_base_snapshot_id", new_callable=AsyncMock, return_value="admin-snap"
        ),
    ):
        assert await server._resolve_snapshot_id({"owner": "acme", "name": "repo"}) == "repo-snap"


@pytest.mark.asyncio
async def test_snapshot_resolution_passes_the_threads_environment() -> None:
    resolve = AsyncMock(return_value={**_READY, "slug": "staging", "snapshot_id": "staging-snap"})
    with (
        patch.object(server, "resolve_environment", resolve),
        patch.object(
            server, "resolve_repo_snapshot_id", new_callable=AsyncMock, return_value="repo-snap"
        ),
        patch.object(
            server, "get_admin_base_snapshot_id", new_callable=AsyncMock, return_value="admin-snap"
        ),
    ):
        snapshot_id = await server._resolve_snapshot_id(None, "staging")

    assert snapshot_id == "staging-snap"
    resolve.assert_awaited_once_with("staging")


def test_environment_slug_reads_the_run_config() -> None:
    assert server._environment_slug({"environment": "staging"}) == "staging"
    assert server._environment_slug({"environment": "  "}) is None
    assert server._environment_slug({}) is None
    assert server._environment_slug(None) is None


# --- admin thread gate ---


def test_admin_thread_requires_flag_and_configured_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramon.nogueira@langchain.dev")
    admin_config = _config(admin_thread=True, user_email="ramon.nogueira@langchain.dev")

    assert server._admin_thread(admin_config, None) is True
    # Same user, no flag: an ordinary thread never gets the tools.
    assert server._admin_thread(_config(user_email="ramon.nogueira@langchain.dev"), None) is False
    # Flag set by a thread whose current requester is not an admin.
    non_admin = _config(admin_thread=True, user_email="someone@else.dev")
    assert server._admin_thread(non_admin, None) is False


def test_admin_thread_accepts_configured_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    assert server._admin_thread(_config(admin_thread=True), "ramonn") is True


def test_workspace_admin_status_does_not_require_admin_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    assert server._workspace_admin(_config(github_login="ramonn"), None) is True


# --- tool gate ---


@pytest.mark.asyncio
async def test_tools_refuse_non_admins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    with patch.object(env_tools, "get_config", return_value=_config(github_login="someone-else")):
        assert await env_tools.list_environments() == {
            "ok": False,
            "error": "Only workspace admins can manage environments.",
        }
        result = await env_tools.save_environment("base", "prompt")
        assert result["ok"] is False


@pytest.mark.asyncio
async def test_capture_tool_requires_a_saved_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    with (
        patch.object(
            env_tools,
            "get_config",
            return_value=_config(github_login="ramonn", thread_id="t-1"),
        ),
        patch.object(env_tools.store, "get_environment", new_callable=AsyncMock, return_value=None),
    ):
        result = await env_tools.capture_environment_snapshot("base")

    assert result["ok"] is False
    assert "save_environment" in result["error"]


# --- prompt wiring ---


def test_sender_context_includes_workspace_admin_status() -> None:
    assert "Workspace admin: yes." in construct_sender_context(None, workspace_admin=True)
    assert "Workspace admin: no." in construct_sender_context(None)


def test_environment_instructions_render_in_system_prompt() -> None:
    prompt = construct_system_prompt(
        working_dir="/workspace",
        environment_name="Base",
        environment_instructions="Checkouts live in /workspace/repos.",
    )
    assert "### Environment Instructions (Base)" in prompt
    assert "Checkouts live in /workspace/repos." in prompt
    assert "### Admin Thread: Environment Setup" not in prompt


def test_admin_section_only_for_admin_threads() -> None:
    assert "### Admin Thread: Environment Setup" in construct_system_prompt(
        working_dir="/workspace", admin_environments=True
    )


def test_blank_environment_prompt_renders_nothing() -> None:
    prompt = construct_system_prompt(
        working_dir="/workspace", environment_name="Base", environment_instructions="   "
    )
    assert "Environment Instructions" not in prompt
