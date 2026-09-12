from contextlib import ExitStack
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph.state import RunnableConfig

from agent import server
from agent.dashboard import environment_refresh as refresh
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
    assert server.environment_slug(RunConfig(environment="staging")) == "staging"
    assert server.environment_slug(RunConfig(environment="  ")) is None
    assert server.environment_slug(RunConfig()) is None


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
        result = await env_tools.publish_environment("base", "prompt")
        assert result["ok"] is False


# --- publish: capture this sandbox, then record ---


class _Publish:
    """Every seam `publish_environment` crosses, patched, with the calls it made."""

    def __init__(self, existing: Environment | None, saved: Environment) -> None:
        self.existing = existing
        self.saved = saved
        self.calls: list[str] = []
        self.capture = AsyncMock(side_effect=self._capture)
        self.publish = AsyncMock(side_effect=self._publish)
        self.discard = AsyncMock()
        self.retire = AsyncMock()
        self.ensure_cron = AsyncMock(return_value="cron-1")
        self._stack = ExitStack()

    async def _capture(self, *_: object, **__: object) -> str:
        self.calls.append("capture")
        return "snap-new"

    async def _publish(self, *_: object, **__: object) -> Environment:
        self.calls.append("write")
        return self.saved

    @property
    def definition(self) -> Any:
        assert self.publish.await_args is not None
        return self.publish.await_args.args[1]

    def __enter__(self) -> _Publish:
        backend = MagicMock()
        backend.id = "sb-thread"
        for target in (
            patch(
                "langgraph_sdk.get_client",
                return_value=MagicMock(
                    threads=MagicMock(
                        get=AsyncMock(
                            return_value={
                                "metadata": {"owner_type": "user", "owner_login": "ramonn"}
                            }
                        )
                    )
                ),
            ),
            patch(
                "agent.run_config.get_config",
                return_value=_config(github_login="ramonn", thread_id="t-1"),
            ),
            patch.object(
                env_tools.store.ENVIRONMENTS,
                "get",
                new_callable=AsyncMock,
                return_value=self.existing,
            ),
            patch(
                "agent.sandboxes.state.get_sandbox_backend",
                new_callable=AsyncMock,
                return_value=backend,
            ),
            patch("agent.sandboxes.state.unwrap_sandbox_backend", side_effect=lambda b: b),
            patch.object(env_tools.store, "capture_sandbox_snapshot", self.capture),
            patch.object(env_tools.store.ENVIRONMENTS, "publish", self.publish),
            patch.object(env_tools.store, "discard_unreferenced_snapshot", self.discard),
            patch.object(env_tools.store, "retire_superseded_snapshot", self.retire),
            patch.object(env_tools.refresh, "ensure_refresh_cron", self.ensure_cron),
        ):
            self._stack.enter_context(target)
        return self

    def __exit__(self, *exc: object) -> None:
        self._stack.close()


def _saved(**fields: Any) -> Environment:
    return Environment(
        slug="base", name="base", snapshot_status="ready", snapshot_id="snap-new", **fields
    )


@pytest.mark.asyncio
async def test_publish_captures_this_sandbox_before_writing_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The record is only ever written once the image it points at exists."""
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    with _Publish(existing=None, saved=_saved(prompt="prompt")) as seams:
        result = await env_tools.publish_environment("base", "prompt")

    assert seams.calls == ["capture", "write"]
    seams.capture.assert_awaited_once_with(
        "sb-thread", "openswe-environment-base", timeout=env_tools.refresh.capture_timeout()
    )
    # Definition and image pointer go in as one write.
    assert seams.publish.await_args is not None
    assert isinstance(seams.definition, env_tools.store.EnvironmentCreate)
    assert seams.publish.await_args.kwargs["snapshot_id"] == "snap-new"
    assert seams.publish.await_args.kwargs["source_sandbox_id"] == "sb-thread"
    assert result["ok"] is True
    assert result["created"] is True
    assert result["environment"]["snapshot_id"] == "snap-new"


@pytest.mark.asyncio
async def test_a_failed_capture_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    with _Publish(existing=None, saved=_saved()) as seams:
        seams.capture.side_effect = RuntimeError("snapshot service unavailable")
        result = await env_tools.publish_environment("base", "prompt")

    assert result["ok"] is False
    assert "snapshot service unavailable" in result["error"]
    seams.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_record_write_discards_the_orphaned_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The capture succeeded, the write did not: nothing is left half-written,
    and the image nothing points at is not left behind either."""
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    with _Publish(existing=None, saved=_saved()) as seams:
        seams.publish.side_effect = RuntimeError("store unavailable")
        result = await env_tools.publish_environment("base", "prompt")

    assert result["ok"] is False
    assert "store unavailable" in result["error"]
    seams.discard.assert_awaited_once_with("base", "snap-new")
    seams.retire.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_bad_definition_is_refused_before_the_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validation is milliseconds; a capture is minutes. Order them accordingly."""
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    with _Publish(existing=None, saved=_saved()) as seams:
        result = await env_tools.publish_environment("base", "prompt", snapshot_name="bad:name")

    assert result["ok"] is False
    seams.capture.assert_not_awaited()


@pytest.mark.asyncio
async def test_publishing_over_an_existing_environment_retires_its_old_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    existing = Environment(
        slug="base", name="base", snapshot_id="snap-old", snapshot_status="ready"
    )
    with _Publish(existing=existing, saved=_saved()) as seams:
        result = await env_tools.publish_environment("base", "prompt")

    assert seams.calls == ["capture", "write"]
    assert isinstance(seams.definition, env_tools.store.EnvironmentUpdate)
    # The old image goes only after the record points at the new one.
    seams.retire.assert_awaited_once_with("base", "snap-old", "snap-new")
    assert result["created"] is False


@pytest.mark.asyncio
async def test_a_setup_script_registers_the_nightly_check_and_its_absence_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    with _Publish(existing=None, saved=_saved(setup_script="make setup")) as seams:
        await env_tools.publish_environment("base", "prompt", setup_script="make setup")
    seams.ensure_cron.assert_awaited_once_with("base")

    with _Publish(existing=None, saved=_saved()) as seams:
        await env_tools.publish_environment("base", "prompt")
    seams.ensure_cron.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_persists_sandbox_sizing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    saved = _saved(
        prompt="prompt",
        mem_bytes=16 * 1024**3,
        vcpus=8,
        fs_capacity_bytes=256 * 1024**3,
        create_params={"_internal_runtime": "v2"},
    )
    with _Publish(existing=None, saved=saved) as seams:
        result = await env_tools.publish_environment(
            "base",
            "prompt",
            mem_bytes=16 * 1024**3,
            vcpus=8,
            fs_capacity_bytes=256 * 1024**3,
            create_params={"_internal_runtime": "v2"},
        )

    definition = seams.definition
    assert definition.mem_bytes == 16 * 1024**3
    assert definition.vcpus == 8
    assert definition.fs_capacity_bytes == 256 * 1024**3
    assert definition.create_params == {"_internal_runtime": "v2"}
    assert result["environment"]["vcpus"] == 8
    assert result["environment"]["create_params"] == {"_internal_runtime": "v2"}


@pytest.mark.asyncio
async def test_publish_can_clear_sandbox_sizing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    with _Publish(existing=Environment(slug="base"), saved=_saved(prompt="prompt")) as seams:
        result = await env_tools.publish_environment(
            "base", "prompt", clear_sizing=True, clear_create_params=True
        )

    definition = seams.definition
    assert {"mem_bytes", "vcpus", "fs_capacity_bytes"} <= definition.model_fields_set
    assert definition.mem_bytes is None
    assert definition.vcpus is None
    assert definition.fs_capacity_bytes is None
    assert definition.create_params == {}
    assert "create_params" in definition.model_fields_set
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_refresh_start_refuses_an_environment_with_no_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    start = AsyncMock()
    with (
        patch("agent.run_config.get_config", return_value=_config(github_login="ramonn")),
        patch.object(
            env_tools.store.ENVIRONMENTS,
            "get",
            new_callable=AsyncMock,
            return_value=Environment(slug="base"),
        ),
        patch.object(env_tools.refresh, "start_refresh_run", start),
    ):
        result = await env_tools.refresh_environment_start("base")

    assert result["status"] == "error"
    assert "setup_script" in result["error"]
    start.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_start_returns_a_task_id_the_unified_poll_understands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Minutes of work, so the tool hands back a task id instead of blocking."""
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
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
            "start_refresh_run",
            new_callable=AsyncMock,
            return_value="run-1",
        ),
    ):
        result = await env_tools.refresh_environment_start("base")

    # Started, not done: the rebuild is still running when this returns.
    assert result["status"] == "started"
    # Prefixed, so `background_task` routes it to the refresh provider.
    assert result["task_id"] == "env-run-1"
    assert refresh.owns_task(result["task_id"])


@pytest.mark.asyncio
async def test_refresh_start_refuses_while_one_is_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    running = Environment(
        slug="base",
        setup_script="make setup",
        refresh_status="refreshing",
        refresh_started_at=datetime.now(UTC).isoformat(),
        refresh_run_id="run-1",
    )
    start = AsyncMock()
    with (
        patch("agent.run_config.get_config", return_value=_config(github_login="ramonn")),
        patch.object(
            env_tools.store.ENVIRONMENTS, "get", new_callable=AsyncMock, return_value=running
        ),
        patch.object(env_tools.refresh, "start_refresh_run", start),
    ):
        result = await env_tools.refresh_environment_start("base")

    assert result["status"] == "error"
    assert result["task_id"] == "env-run-1"
    start.assert_not_awaited()


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
