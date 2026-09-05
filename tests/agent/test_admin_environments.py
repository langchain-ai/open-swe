from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.graph.state import RunnableConfig

from agent import server
from agent.dashboard.environments import Environment
from agent.prompt import construct_sender_context, construct_system_prompt
from agent.run_config import RunConfig
from agent.sandboxes import lifecycle
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
        assert (await lifecycle.SandboxCreateConfig.resolve()).snapshot_id == "env-snap"


@pytest.mark.asyncio
async def test_environment_without_a_captured_snapshot_falls_back_to_base() -> None:
    never_captured = _READY.model_copy(update={"snapshot_status": "failed", "snapshot_id": None})
    with (
        patch.object(
            lifecycle, "resolve_environment", new_callable=AsyncMock, return_value=never_captured
        ),
        patch.object(
            lifecycle,
            "get_admin_base_snapshot_id",
            new_callable=AsyncMock,
            return_value="admin-snap",
        ),
    ):
        assert (await lifecycle.SandboxCreateConfig.resolve()).snapshot_id == "admin-snap"


@pytest.mark.asyncio
async def test_a_nightly_capture_does_not_send_runs_to_the_base_image() -> None:
    """The new id lands only on success, so a refresh in flight changes nothing."""
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
        assert (await lifecycle.SandboxCreateConfig.resolve()).snapshot_id == "env-snap"


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
        snapshot_id = (await lifecycle.SandboxCreateConfig.resolve("staging")).snapshot_id

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
        config = await lifecycle.SandboxCreateConfig.resolve("base")
        snapshot_id = config.snapshot_id
        resources = config.resources
        create_params = config.create_params

    assert snapshot_id == "env-snap"
    assert resources == {
        "mem_bytes": 32 * 1024**3,
        "vcpus": 16,
        "fs_capacity_bytes": 512 * 1024**3,
    }
    assert create_params == {"_internal_runtime": "v2"}


def test_environment_slug_reads_the_run_config() -> None:
    assert server._environment_slug(RunConfig(environment="staging")) == "staging"
    assert server._environment_slug(RunConfig(environment="  ")) is None
    assert server._environment_slug(RunConfig()) is None


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
    with patch("agent.run_config.get_config", return_value=_config(github_login="someone-else")):
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
        patch("agent.run_config.get_config", return_value=_config(github_login="ramonn")),
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
        patch("agent.run_config.get_config", return_value=_config(github_login="ramonn")),
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
    with patch("agent.run_config.get_config", return_value=_config(github_login="ramonn")):
        result = await env_tools.save_environment("base", "prompt", vcpus=8, clear_sizing=True)

    assert result == {
        "ok": False,
        "error": "clear_sizing cannot be combined with sizing values",
    }


@pytest.mark.asyncio
async def test_refresh_tool_requires_a_saved_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    with (
        patch(
            "agent.run_config.get_config",
            return_value=_config(github_login="ramonn", thread_id="t-1"),
        ),
        patch.object(
            env_tools.store.ENVIRONMENTS, "get", new_callable=AsyncMock, return_value=None
        ),
    ):
        result = await env_tools.refresh_environment("base")

    assert result["ok"] is False
    assert "save_environment" in result["error"]


@pytest.mark.asyncio
async def test_refresh_tool_refuses_an_environment_with_no_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    run_refresh = AsyncMock()
    with (
        patch("agent.run_config.get_config", return_value=_config(github_login="ramonn")),
        patch.object(
            env_tools.store.ENVIRONMENTS,
            "get",
            new_callable=AsyncMock,
            return_value=Environment(slug="base"),
        ),
        patch.object(env_tools.refresh, "refresh_environment", run_refresh),
    ):
        result = await env_tools.refresh_environment("base")

    assert result["ok"] is False
    assert "setup_script" in result["error"]
    run_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_tool_waits_and_hands_back_the_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The admin thread iterates on the script, so it needs the error, not a job id."""
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    outcome = {
        "status": "failed",
        "script": "init",
        "error": "init script exited 1",
        "log": "fatal: not a git repository",
    }
    with (
        patch("agent.run_config.get_config", return_value=_config(github_login="ramonn")),
        patch.object(
            env_tools.store.ENVIRONMENTS,
            "get",
            new_callable=AsyncMock,
            return_value=Environment(slug="base", setup_script="make setup"),
        ),
        patch.object(
            env_tools.refresh,
            "refresh_environment",
            new_callable=AsyncMock,
            return_value=outcome,
        ),
    ):
        result = await env_tools.refresh_environment("base")

    assert result["ok"] is False
    assert result["failed_script"] == "init"
    assert result["error"] == "init script exited 1"
    assert result["log"] == "fatal: not a git repository"


@pytest.mark.asyncio
async def test_saving_a_setup_script_runs_it_and_registers_the_cron(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    record = Environment(slug="base", name="base", setup_script="make setup")
    ensure_cron = AsyncMock(return_value="cron-1")
    run_refresh = AsyncMock(return_value={"status": "success", "seconds": 12, "log": "done"})
    with (
        patch("agent.run_config.get_config", return_value=_config(github_login="ramonn")),
        patch.object(
            env_tools.store.ENVIRONMENTS,
            "get",
            new_callable=AsyncMock,
            side_effect=[None, record],
        ),
        patch.object(
            env_tools.store.ENVIRONMENTS, "create", new_callable=AsyncMock, return_value=record
        ),
        patch.object(env_tools.refresh, "ensure_refresh_cron", ensure_cron),
        patch.object(env_tools.refresh, "refresh_environment", run_refresh),
    ):
        result = await env_tools.save_environment("base", "prompt", setup_script="make setup")

    assert result["ok"] is True
    assert result["environment"]["setup_script"] == "make setup"
    assert result["refresh"] == {"ok": True, "refreshed": True, "seconds": 12, "log": "done"}
    run_refresh.assert_awaited_once_with("base")
    ensure_cron.assert_awaited_once_with("base")


@pytest.mark.asyncio
async def test_a_prompt_only_save_does_not_rebuild(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rebuild costs minutes; only a changed script has earned one."""
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    existing = Environment(slug="base", name="base", setup_script="make setup")
    run_refresh = AsyncMock()
    with (
        patch("agent.run_config.get_config", return_value=_config(github_login="ramonn")),
        patch.object(
            env_tools.store.ENVIRONMENTS, "get", new_callable=AsyncMock, return_value=existing
        ),
        patch.object(
            env_tools.store.ENVIRONMENTS,
            "apply_update",
            new_callable=AsyncMock,
            return_value=existing.model_copy(update={"prompt": "new"}),
        ),
        patch.object(env_tools.refresh, "ensure_refresh_cron", AsyncMock()),
        patch.object(env_tools.refresh, "refresh_environment", run_refresh),
    ):
        result = await env_tools.save_environment("base", "new")

    assert result["ok"] is True
    assert "refresh" not in result
    run_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_rebuild_makes_the_save_report_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    record = Environment(slug="base", name="base", setup_script="make setup")
    with (
        patch("agent.run_config.get_config", return_value=_config(github_login="ramonn")),
        patch.object(
            env_tools.store.ENVIRONMENTS, "get", new_callable=AsyncMock, return_value=None
        ),
        patch.object(
            env_tools.store.ENVIRONMENTS, "create", new_callable=AsyncMock, return_value=record
        ),
        patch.object(env_tools.refresh, "ensure_refresh_cron", AsyncMock()),
        patch.object(
            env_tools.refresh,
            "refresh_environment",
            new_callable=AsyncMock,
            return_value={"status": "failed", "script": "setup", "error": "boom", "log": "gcc: no"},
        ),
    ):
        result = await env_tools.save_environment("base", "prompt", setup_script="make setup")

    assert result["ok"] is False
    assert result["refresh"]["failed_script"] == "setup"
    assert result["refresh"]["log"] == "gcc: no"


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
    assert "`setup_script`" in prompt
    assert "Every environment must include `rg` and `gh`" in prompt
    assert "direct them to an admin thread" not in prompt


def test_blank_environment_prompt_renders_nothing() -> None:
    prompt = construct_system_prompt(
        working_dir="/workspace", environment_name="Base", environment_instructions="   "
    )
    assert "Environment Instructions" not in prompt
