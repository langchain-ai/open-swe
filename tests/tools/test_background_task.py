"""The one poll tool, across both kinds of background work."""

from typing import Any, Literal, cast
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.graph.state import RunnableConfig

from openswe.execution_images import EXECUTION_IMAGES, ImageState
from openswe.sandboxes.image_build import RefreshStep, SnapshotBuild
from openswe.tools.background_task import background_task
from tests.conftest import serve_threads_from

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.fixture
def admin(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Global image builds are admin-only, so most of these run as one."""
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    config = cast(
        RunnableConfig,
        {"configurable": {"github_login": "ramonn", "thread_id": "admin", "admin_thread": True}},
    )
    serve_threads_from(
        monkeypatch,
        AsyncMock(return_value={"metadata": {"visibility": "private", "owner_type": "user"}}),
    )
    with patch("openswe.run_config.get_config", return_value=config):
        yield


@pytest.fixture
def member(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    config = cast(RunnableConfig, {"configurable": {"github_login": "someone-else"}})
    with patch("openswe.run_config.get_config", return_value=config):
        yield


def _running(**overrides: Any) -> ImageState:
    return ImageState(
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


def _state(record: ImageState) -> Any:
    return patch.object(EXECUTION_IMAGES, "state", new_callable=AsyncMock, return_value=record)


@pytest.mark.asyncio
async def test_a_running_refresh_reports_its_step_and_the_live_trace(admin: Any) -> None:
    """A rebuild is minutes to an hour, so the step it reached is the answer."""
    with (
        _state(_running()),
        patch.object(
            SnapshotBuild,
            "read_builder_log",
            new_callable=AsyncMock,
            return_value="+ apt-get install -y ripgrep",
        ) as read_log,
    ):
        result = await background_task("status", "image-run-1")

    assert result["success"] is True
    assert result["status"] == "running"
    assert result["kind"] == "execution_image_refresh"
    assert result["step"] == "setup"
    assert [step["label"] for step in result["steps"]] == ["boot", "setup"]
    assert result["output"] == "+ apt-get install -y ripgrep"
    assert result["output_source"] == "builder"
    read_log.assert_awaited_once_with("sb-builder", "/open-swe/environment/logs/setup.log")


@pytest.mark.asyncio
async def test_one_provider_failing_does_not_blank_the_listing(admin: Any) -> None:
    """No sandbox bound to the thread still leaves refreshes visible."""
    with (
        _state(_running()),
        patch(
            "openswe.tools.background_execute.task_list",
            new_callable=AsyncMock,
            side_effect=RuntimeError("No sandbox is bound to this thread"),
        ),
    ):
        result = await background_task("list")

    assert result["success"] is True
    assert [task["kind"] for task in result["tasks"]] == ["execution_image_refresh"]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["status", "list", "stop"])
async def test_shared_admin_threads_cannot_access_image_tasks(
    admin: object, monkeypatch: pytest.MonkeyPatch, action: Literal["status", "list", "stop"]
) -> None:
    serve_threads_from(
        monkeypatch,
        AsyncMock(
            return_value={
                "metadata": {"visibility": "public", "owner_type": "user", "admin_thread": True}
            }
        ),
    )
    command = {"task_id": "cmd-1", "kind": "sandbox_command", "status": "running"}
    with (
        patch.object(
            EXECUTION_IMAGES,
            "task_status",
            AsyncMock(return_value={"output": "private build trace"}),
        ) as status,
        patch.object(
            EXECUTION_IMAGES, "task_stop", AsyncMock(return_value={"output": "private build trace"})
        ) as stop,
        patch.object(
            EXECUTION_IMAGES,
            "task_list",
            AsyncMock(return_value=[{"output": "private build trace"}]),
        ) as listing,
        patch("openswe.tools.background_execute.task_list", AsyncMock(return_value=[command])),
    ):
        result = await background_task(action, "image-run-1" if action != "list" else None)

    status.assert_not_awaited()
    stop.assert_not_awaited()
    listing.assert_not_awaited()
    if action == "list":
        assert result == {"success": True, "tasks": [command]}
    else:
        assert result["success"] is False
        assert "private" in result["error"]
        assert "output" not in result


@pytest.mark.asyncio
async def test_a_non_admin_cannot_read_a_refresh(member: Any) -> None:
    """Robin-wide state and live script output are restricted to admins."""
    with _state(_running()):
        result = await background_task("status", "image-run-1")

    assert result["success"] is False
    assert "admins" in result["error"]
    assert "output" not in result


@pytest.mark.asyncio
async def test_a_non_admin_cannot_cancel_a_rebuild(member: Any) -> None:
    """A stop would cancel a rebuild every other run depends on."""
    stop = AsyncMock()
    with _state(_running()), patch.object(EXECUTION_IMAGES, "task_stop", stop):
        result = await background_task("stop", "image-run-1")

    assert result["success"] is False
    stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_non_admin_lists_only_their_own_commands(member: Any) -> None:
    """Refreshes drop out of the listing rather than failing it."""
    with (
        _state(_running()),
        patch(
            "openswe.tools.background_execute.task_list",
            new_callable=AsyncMock,
            return_value=[{"task_id": "cmd-1", "kind": "sandbox_command", "status": "running"}],
        ),
    ):
        result = await background_task("list")

    assert result["success"] is True
    assert [task["kind"] for task in result["tasks"]] == ["sandbox_command"]
