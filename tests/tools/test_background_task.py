"""The one poll tool, across both kinds of background work."""

from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.graph.state import RunnableConfig

from agent.tools.background_task import background_task
from agent.workspaces import refresh
from agent.workspaces.store import RefreshStep, Workspace


@pytest.fixture
def admin(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Workspace refreshes are admin-only, so most of these run as one."""
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


def _running(**overrides: Any) -> Workspace:
    return Workspace(
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


def _store(*records: Workspace) -> Any:
    return patch.object(
        refresh.WORKSPACES, "list_all", new_callable=AsyncMock, return_value=list(records)
    )


# --- routing ---


def test_a_task_id_routes_to_exactly_one_provider() -> None:
    """The command provider claims everything the refresh provider does not."""
    from agent.tools.background_execute import owns_task as command_owns

    assert refresh.owns_task("ws-run-1")
    assert not command_owns("ws-run-1")
    # Handles minted before the rename still route to the refresh provider.
    assert refresh.owns_task("env-run-1")
    assert not command_owns("env-run-1")
    assert command_owns("cmd-abc")
    # Ids minted before task kinds existed still resolve to the command provider.
    assert command_owns("2b1c4f9e-0000-4000-8000-000000000000")


# --- workspace refreshes ---


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
        result = await background_task("status", "ws-run-1")

    assert result["success"] is True
    assert result["status"] == "running"
    assert result["kind"] == "workspace_refresh"
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
        result = await background_task("status", "ws-run-1")

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
        result = await background_task("status", "ws-run-1")

    assert result["status"] == "failed"
    assert result["error"] == "setup script exited 2"
    assert result["output"] == "gcc: fatal error"
    assert result["output_source"] == "record"


@pytest.mark.asyncio
async def test_a_superseded_handle_resolves_to_nothing(admin: Any) -> None:
    """Keyed on the run, so a stale id never reports a later refresh's progress."""
    with _store(_running(refresh_run_id="run-2")):
        result = await background_task("status", "ws-run-1")

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
        "workspace_refresh",
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
    assert [task["kind"] for task in result["tasks"]] == ["workspace_refresh"]


# --- authorization ---


@pytest.mark.asyncio
async def test_a_non_admin_cannot_read_a_refresh(member: Any) -> None:
    """Workspace-wide state, and a `bash -x` trace expands its arguments."""
    with _store(_running()):
        result = await background_task("status", "ws-run-1")

    assert result["success"] is False
    assert "Only workspace admins" in result["error"]
    assert "output" not in result


@pytest.mark.asyncio
async def test_a_non_admin_cannot_cancel_a_rebuild(member: Any) -> None:
    """A stop would cancel a rebuild every other run depends on."""
    stop = AsyncMock()
    with _store(_running()), patch.object(refresh, "task_stop", stop):
        result = await background_task("stop", "ws-run-1")

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


# --- waiting and throttling ---


@pytest.mark.asyncio
async def test_wait_returns_after_completion(admin: Any) -> None:
    status = AsyncMock(
        side_effect=[
            {"task_id": "cmd-1", "status": "running"},
            {"task_id": "cmd-1", "status": "completed", "output": "done"},
        ]
    )
    with (
        patch("agent.tools.background_task.asyncio.sleep", new_callable=AsyncMock),
        patch("agent.tools.background_execute.task_status", status),
    ):
        result = await background_task("wait", "cmd-1", timeout=10)

    assert result["success"] is True
    assert result["task_id"] == "cmd-1"
    assert result["status"] == "completed"
    assert result["output"] == "done"
    assert result["timed_out"] is False
    assert result["waited_seconds"] >= 0
    assert status.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", ["completed", "failed", "stopped", "timed_out", "lost", "stop_requested"]
)
async def test_wait_does_not_sleep_for_terminal_status(admin: Any, status: str) -> None:
    with (
        patch(
            "agent.tools.background_execute.task_status",
            new_callable=AsyncMock,
            return_value={"task_id": "cmd-1", "status": status},
        ),
        patch("agent.tools.background_task.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        result = await background_task("wait", "cmd-1")

    assert result["status"] == status
    assert result["waited_seconds"] == 0
    assert result["timed_out"] is False
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_wait_caps_timeout_and_reports_deadline(
    monkeypatch: pytest.MonkeyPatch, admin: Any
) -> None:
    clock = [0.0]

    async def advance(seconds: float) -> None:
        clock[0] += 300

    with (
        patch("agent.tools.background_task.time.monotonic", side_effect=lambda: clock[0]),
        patch("agent.tools.background_task.asyncio.sleep", side_effect=advance) as sleep,
        patch(
            "agent.tools.background_execute.task_status",
            new_callable=AsyncMock,
            return_value={"task_id": "cmd-1", "status": "running"},
        ),
    ):
        result = await background_task("wait", "cmd-1", timeout=999)

    assert result["timed_out"] is True
    assert result["waited_seconds"] == 300
    sleep.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_status_throttling_is_scoped_to_model_run(monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"configurable": {"github_login": "ramonn", "run_id": "run-a"}}
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    with (
        patch("agent.run_config.get_config", return_value=config),
        patch(
            "agent.tools.background_execute.task_status",
            new_callable=AsyncMock,
            return_value={"task_id": "cmd-1", "status": "running"},
        ),
    ):
        import importlib

        background_task_module = importlib.import_module("agent.tools.background_task")

        background_task_module._STATUS_READS.clear()
        results = [await background_task("status", "cmd-1") for _ in range(6)]
        config["configurable"]["run_id"] = "run-b"
        next_run = await background_task("status", "cmd-1")
        background_task_module._STATUS_READS.clear()

    assert "guidance" not in results[4]
    assert "guidance" in results[5]
    assert "guidance" not in next_run


def test_prompts_document_wait_instead_of_status_polling() -> None:
    task_prompt = Path("agent/resources/prompts/tools/background_task.md").read_text()
    execute_prompt = Path("agent/resources/prompts/tools/background_execute.md").read_text()
    assert "`wait`" in task_prompt
    assert "Never repeatedly call `status`" in task_prompt
    assert 'action="wait"' in execute_prompt
