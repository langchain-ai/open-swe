"""The one poll tool, across both kinds of background work."""

from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.graph.state import RunnableConfig

from agent.dashboard import environment_refresh as refresh
from agent.dashboard.environments import Environment, RefreshStep
from agent.tools.background_task import background_task


@pytest.fixture
def admin(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Environment refreshes are admin-only, so most of these run as one."""
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    config = cast(RunnableConfig, {"configurable": {"github_login": "ramonn"}})
    with patch("agent.run_config.get_config", return_value=config):
        yield


@pytest.fixture
def member(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    config = cast(RunnableConfig, {"configurable": {"github_login": "someone-else"}})
    with patch("agent.run_config.get_config", return_value=config):
        yield


def _running(**overrides: Any) -> Environment:
    return Environment(
        slug="base",
        refresh_status="refreshing",
        refresh_kind="full",
        refresh_run_id="run-1",
        refresh_started_at="2026-09-08T10:00:00+00:00",
        refresh_sandbox_id="sb-builder",
        refresh_steps=[
            RefreshStep(label="boot", status="success", started_at="2026-09-08T10:00:00+00:00"),
            RefreshStep(
                label="setup",
                started_at="2026-09-08T10:01:00+00:00",
                log_path="/open-swe/environment/logs/setup.log",
            ),
        ],
    ).model_copy(update=overrides)


def _store(*records: Environment) -> Any:
    return patch.object(
        refresh.ENVIRONMENTS, "list_all", new_callable=AsyncMock, return_value=list(records)
    )


# --- routing ---


def test_a_task_id_routes_to_exactly_one_provider() -> None:
    """The command provider claims everything the refresh provider does not."""
    from agent.tools.background_execute import owns_task as command_owns

    assert refresh.owns_task("env-run-1")
    assert not command_owns("env-run-1")
    assert command_owns("cmd-abc")
    # Ids minted before task kinds existed still resolve to the command provider.
    assert command_owns("2b1c4f9e-0000-4000-8000-000000000000")


@pytest.mark.asyncio
async def test_status_and_stop_need_a_task_id() -> None:
    assert (await background_task("status"))["success"] is False
    assert "task_id is required" in (await background_task("stop"))["error"]


# --- environment refreshes ---


@pytest.mark.asyncio
async def test_a_running_refresh_reports_its_step_and_the_live_trace(admin: Any) -> None:
    """A rebuild is minutes to an hour, so the step it reached is the answer."""
    with (
        _store(_running()),
        patch.object(
            refresh,
            "_read_builder_log",
            new_callable=AsyncMock,
            return_value="+ apt-get install -y ripgrep",
        ) as read_log,
    ):
        result = await background_task("status", "env-run-1")

    assert result["success"] is True
    assert result["status"] == "running"
    assert result["kind"] == "environment_refresh"
    assert result["step"] == "setup"
    assert [step["label"] for step in result["steps"]] == ["boot", "setup"]
    assert result["output"] == "+ apt-get install -y ripgrep"
    assert result["output_source"] == "builder"
    read_log.assert_awaited_once_with("sb-builder", "/open-swe/environment/logs/setup.log")


@pytest.mark.asyncio
async def test_an_unreadable_builder_costs_the_trace_not_the_poll(admin: Any) -> None:
    """The box is released at capture; a poll that lands after must still answer."""
    with (
        _store(_running()),
        patch.object(refresh, "_read_builder_log", new_callable=AsyncMock, return_value=None),
    ):
        result = await background_task("status", "env-run-1")

    assert result["success"] is True
    assert result["step"] == "setup"
    assert result["output"] is None


@pytest.mark.asyncio
async def test_a_settled_refresh_reads_its_log_off_the_record(admin: Any) -> None:
    settled = _running(
        refresh_status="failed",
        refresh_error="setup script exited 2",
        refresh_log="gcc: fatal error",
        refresh_sandbox_id=None,
        refresh_finished_at="2026-09-08T10:30:00+00:00",
        refresh_steps=[RefreshStep(label="setup", status="failed", exit_code=2)],
    )
    with _store(settled):
        result = await background_task("status", "env-run-1")

    assert result["status"] == "failed"
    assert result["error"] == "setup script exited 2"
    assert result["output"] == "gcc: fatal error"
    assert result["output_source"] == "record"


@pytest.mark.asyncio
async def test_a_superseded_handle_resolves_to_nothing(admin: Any) -> None:
    """Keyed on the run, so a stale id never reports a later refresh's progress."""
    with _store(_running(refresh_run_id="run-2")):
        result = await background_task("status", "env-run-1")

    assert result["error"] == "task not found"
    assert "superseded" in result["detail"]


# --- listing ---


@pytest.mark.asyncio
async def test_list_merges_both_kinds(admin: Any) -> None:
    with (
        _store(_running()),
        patch(
            "agent.tools.background_execute.task_list",
            new_callable=AsyncMock,
            return_value=[{"task_id": "cmd-1", "kind": "sandbox_command", "status": "running"}],
        ),
    ):
        result = await background_task("list")

    assert {task["kind"] for task in result["tasks"]} == {
        "environment_refresh",
        "sandbox_command",
    }


@pytest.mark.asyncio
async def test_one_provider_failing_does_not_blank_the_listing(admin: Any) -> None:
    """No sandbox bound to the thread still leaves refreshes visible."""
    with (
        _store(_running()),
        patch(
            "agent.tools.background_execute.task_list",
            new_callable=AsyncMock,
            side_effect=RuntimeError("No sandbox is bound to this thread"),
        ),
    ):
        result = await background_task("list")

    assert result["success"] is True
    assert [task["kind"] for task in result["tasks"]] == ["environment_refresh"]


@pytest.mark.asyncio
async def test_a_never_refreshed_environment_is_not_a_task(admin: Any) -> None:
    with (
        _store(Environment(slug="base"), _running()),
        patch("agent.tools.background_execute.task_list", new_callable=AsyncMock, return_value=[]),
    ):
        result = await background_task("list")

    assert [task["environment"] for task in result["tasks"]] == ["base"]
    assert len(result["tasks"]) == 1


# --- authorization ---


@pytest.mark.asyncio
async def test_a_non_admin_cannot_read_a_refresh(member: Any) -> None:
    """Workspace-wide state, and a `bash -x` trace expands its arguments."""
    with _store(_running()):
        result = await background_task("status", "env-run-1")

    assert result["success"] is False
    assert "Only workspace admins" in result["error"]
    assert "output" not in result


@pytest.mark.asyncio
async def test_a_non_admin_cannot_cancel_a_rebuild(member: Any) -> None:
    """A stop would cancel a rebuild every other run depends on."""
    stop = AsyncMock()
    with _store(_running()), patch.object(refresh, "task_stop", stop):
        result = await background_task("stop", "env-run-1")

    assert result["success"] is False
    stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_non_admin_lists_only_their_own_commands(member: Any) -> None:
    """Refreshes drop out of the listing rather than failing it."""
    with (
        _store(_running()),
        patch(
            "agent.tools.background_execute.task_list",
            new_callable=AsyncMock,
            return_value=[{"task_id": "cmd-1", "kind": "sandbox_command", "status": "running"}],
        ),
    ):
        result = await background_task("list")

    assert result["success"] is True
    assert [task["kind"] for task in result["tasks"]] == ["sandbox_command"]


@pytest.mark.asyncio
async def test_a_non_admin_still_reads_their_own_command(member: Any) -> None:
    with patch(
        "agent.tools.background_execute.task_status",
        new_callable=AsyncMock,
        return_value={"task_id": "cmd-1", "status": "running", "output": "ok"},
    ):
        result = await background_task("status", "cmd-1")

    assert result["success"] is True
    assert result["output"] == "ok"
