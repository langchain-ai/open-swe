"""The one poll tool, across both kinds of background work."""

from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.graph.state import RunnableConfig

from openswe.tools.background_task import background_task
from openswe.workspaces import refresh
from openswe.workspaces.store import RefreshStep, Workspace


@pytest.fixture
def admin(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Workspace refreshes are admin-only, so most of these run as one."""
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    config = cast(RunnableConfig, {"configurable": {"github_login": "ramonn"}})
    with patch("openswe.run_config.get_config", return_value=config):
        yield


@pytest.fixture
def member(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    config = cast(RunnableConfig, {"configurable": {"github_login": "someone-else"}})
    with patch("openswe.run_config.get_config", return_value=config):
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


# --- listing ---


@pytest.mark.asyncio
async def test_one_provider_failing_does_not_blank_the_listing(admin: Any) -> None:
    """No sandbox bound to the thread still leaves refreshes visible."""
    with (
        _store(_running()),
        patch(
            "openswe.tools.background_execute.task_list",
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
            "openswe.tools.background_execute.task_list",
            new_callable=AsyncMock,
            return_value=[{"task_id": "cmd-1", "kind": "sandbox_command", "status": "running"}],
        ),
    ):
        result = await background_task("list")

    assert result["success"] is True
    assert [task["kind"] for task in result["tasks"]] == ["sandbox_command"]
