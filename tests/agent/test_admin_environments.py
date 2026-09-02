from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.graph.state import RunnableConfig

from agent import server
from agent.dashboard.environments import Environment
from agent.prompt import construct_sender_context, construct_system_prompt
from agent.sandboxes import lifecycle
from agent.tools import admin_gate
from agent.tools import environments as env_tools

_READY = Environment(slug="base", name="Base", snapshot_status="ready", snapshot_id="env-snap")


def _config(**configurable: object) -> RunnableConfig:
    return cast(RunnableConfig, {"configurable": configurable})


# --- snapshot precedence ---


@pytest.mark.asyncio
async def test_default_environment_snapshot_wins_over_base() -> None:
    with (
        patch.object(lifecycle, "resolve_environment", new_callable=AsyncMock, return_value=_READY),
        patch.object(
            lifecycle,
            "get_admin_base_snapshot_id",
            new_callable=AsyncMock,
            return_value="admin-snap",
        ),
    ):
        assert await lifecycle._resolve_snapshot_id() == "env-snap"


@pytest.mark.asyncio
async def test_environment_without_ready_snapshot_falls_back_to_base() -> None:
    capturing = _READY.model_copy(update={"snapshot_status": "capturing"})
    with (
        patch.object(
            lifecycle, "resolve_environment", new_callable=AsyncMock, return_value=capturing
        ),
        patch.object(
            lifecycle,
            "get_admin_base_snapshot_id",
            new_callable=AsyncMock,
            return_value="admin-snap",
        ),
    ):
        assert await lifecycle._resolve_snapshot_id() == "admin-snap"


@pytest.mark.asyncio
async def test_snapshot_resolution_passes_the_threads_environment() -> None:
    resolve = AsyncMock(
        return_value=_READY.model_copy(update={"slug": "staging", "snapshot_id": "staging-snap"})
    )
    with (
        patch.object(lifecycle, "resolve_environment", resolve),
        patch.object(
            lifecycle,
            "get_admin_base_snapshot_id",
            new_callable=AsyncMock,
            return_value="admin-snap",
        ),
    ):
        snapshot_id = await lifecycle._resolve_snapshot_id("staging")

    assert snapshot_id == "staging-snap"
    resolve.assert_awaited_once_with("staging")


@pytest.mark.asyncio
async def test_environment_sandbox_sizing_is_resolved_with_snapshot() -> None:
    environment = _READY.model_copy(
        update={
            "mem_bytes": 32 * 1024**3,
            "vcpus": 16,
            "fs_capacity_bytes": 512 * 1024**3,
            "create_params": {"_internal_runtime": "v2"},
        }
    )
    with patch.object(
        lifecycle, "resolve_environment", new_callable=AsyncMock, return_value=environment
    ):
        snapshot_id, resources, create_params = await lifecycle._resolve_sandbox_create_config(
            "base"
        )

    assert snapshot_id == "env-snap"
    assert resources == {
        "mem_bytes": 32 * 1024**3,
        "vcpus": 16,
        "fs_capacity_bytes": 512 * 1024**3,
    }
    assert create_params == {"_internal_runtime": "v2"}


def test_environment_slug_reads_the_run_config() -> None:
    assert server._environment_slug({"environment": "staging"}) == "staging"
    assert server._environment_slug({"environment": "  "}) is None
    assert server._environment_slug({}) is None
    assert server._environment_slug(None) is None


# --- admin thread gate ---


@pytest.mark.asyncio
async def test_admin_thread_requires_flag_and_configured_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramon.nogueira@langchain.dev")
    admin_config = _config(admin_thread=True, user_email="ramon.nogueira@langchain.dev")

    assert await server._admin_thread(admin_config, None) is True
    # Same user, no flag: an ordinary thread never gets the tools.
    assert (
        await server._admin_thread(_config(user_email="ramon.nogueira@langchain.dev"), None)
        is False
    )
    # Flag set by a thread whose current requester is not an admin.
    non_admin = _config(admin_thread=True, user_email="someone@else.dev")
    assert await server._admin_thread(non_admin, None) is False


@pytest.mark.asyncio
async def test_admin_thread_accepts_configured_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    assert await server._admin_thread(_config(admin_thread=True), "ramonn") is True


@pytest.mark.asyncio
async def test_workspace_admin_resolves_email_for_github_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramon@langchain.dev")
    with patch.object(
        server, "email_for_login", new_callable=AsyncMock, return_value="ramon@langchain.dev"
    ):
        assert await server._workspace_admin(_config(github_login="ramonn"), None) is True


# --- tool gate ---


@pytest.mark.asyncio
async def test_tools_refuse_non_admins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    with patch.object(admin_gate, "get_config", return_value=_config(github_login="someone-else")):
        assert await env_tools.list_environments() == {
            "ok": False,
            "error": "Only workspace admins can manage environments.",
        }
        result = await env_tools.save_environment("base", "prompt")
        assert result["ok"] is False


@pytest.mark.asyncio
async def test_save_environment_persists_sandbox_sizing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    create = AsyncMock(
        return_value=Environment(
            slug="base",
            name="base",
            prompt="prompt",
            mem_bytes=16 * 1024**3,
            vcpus=8,
            fs_capacity_bytes=256 * 1024**3,
            create_params={"_internal_runtime": "v2"},
        )
    )
    with (
        patch.object(admin_gate, "get_config", return_value=_config(github_login="ramonn")),
        patch.object(
            env_tools.store.ENVIRONMENTS, "get", new_callable=AsyncMock, return_value=None
        ),
        patch.object(env_tools.store.ENVIRONMENTS, "create", create),
    ):
        result = await env_tools.save_environment(
            "base",
            "prompt",
            mem_bytes=16 * 1024**3,
            vcpus=8,
            fs_capacity_bytes=256 * 1024**3,
            create_params={"_internal_runtime": "v2"},
        )

    assert create.await_args is not None
    saved = create.await_args.args[0]
    assert saved.mem_bytes == 16 * 1024**3
    assert saved.vcpus == 8
    assert saved.fs_capacity_bytes == 256 * 1024**3
    assert saved.create_params == {"_internal_runtime": "v2"}
    assert result["environment"]["vcpus"] == 8
    assert result["environment"]["create_params"] == {"_internal_runtime": "v2"}


@pytest.mark.asyncio
async def test_save_environment_can_clear_sandbox_sizing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    update = AsyncMock(return_value=Environment(slug="base", name="base", prompt="prompt"))
    with (
        patch.object(admin_gate, "get_config", return_value=_config(github_login="ramonn")),
        patch.object(
            env_tools.store.ENVIRONMENTS,
            "get",
            new_callable=AsyncMock,
            return_value=Environment(slug="base"),
        ),
        patch.object(env_tools.store.ENVIRONMENTS, "apply_update", update),
    ):
        result = await env_tools.save_environment(
            "base",
            "prompt",
            clear_sizing=True,
            clear_create_params=True,
        )

    assert update.await_args is not None
    saved = update.await_args.args[1]
    assert {"mem_bytes", "vcpus", "fs_capacity_bytes"} <= saved.model_fields_set
    assert saved.mem_bytes is None
    assert saved.vcpus is None
    assert saved.fs_capacity_bytes is None
    assert saved.create_params == {}
    assert "create_params" in saved.model_fields_set
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_save_environment_rejects_clear_sizing_with_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    with patch.object(admin_gate, "get_config", return_value=_config(github_login="ramonn")):
        result = await env_tools.save_environment("base", "prompt", vcpus=8, clear_sizing=True)

    assert result == {
        "ok": False,
        "error": "clear_sizing cannot be combined with sizing values",
    }


@pytest.mark.asyncio
async def test_capture_tool_requires_a_saved_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    with (
        patch.object(
            admin_gate,
            "get_config",
            return_value=_config(github_login="ramonn", thread_id="t-1"),
        ),
        patch.object(
            env_tools.store.ENVIRONMENTS, "get", new_callable=AsyncMock, return_value=None
        ),
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
    assert "### Admin Thread: Workspace Setup" not in prompt


def test_admin_section_only_for_admin_threads() -> None:
    prompt = construct_system_prompt(working_dir="/workspace", admin_environments=True)
    assert "### Admin Thread: Workspace Setup" in prompt
    assert "optional VM sizing" in prompt
    assert "Every environment must include `rg` and `gh`" in prompt
    assert "direct them to an admin thread" not in prompt


def test_blank_environment_prompt_renders_nothing() -> None:
    prompt = construct_system_prompt(
        working_dir="/workspace", environment_name="Base", environment_instructions="   "
    )
    assert "Environment Instructions" not in prompt
