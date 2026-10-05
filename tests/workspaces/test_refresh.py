import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.workspaces import refresh
from agent.workspaces.store import WORKSPACES, WorkspaceCreate


class _Result:
    def __init__(self, output: str, exit_code: int) -> None:
        self.output = output
        self.exit_code = exit_code


def _backend(*results: _Result) -> MagicMock:
    backend = MagicMock()
    backend.id = "sb-builder"
    backend.aexecute = AsyncMock(side_effect=list(results))
    return backend


def _scripts_run(backend: MagicMock) -> list[str]:
    """The script bodies the builder actually executed, in order."""
    bodies = []
    for call in backend.aexecute.call_args_list:
        encoded = str(call.args[0]).split("printf %s ")[1].split(" |")[0].strip("'")
        bodies.append(base64.b64decode(encoded).decode())
    return bodies


# --- script command (sync) ---


# --- refresh ---


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
async def test_the_update_script_runs_after_setup_and_gates_the_capture() -> None:
    """A broken update script must be caught here, not by the next hourly update."""
    backend = _backend(_Result("provisioned", 0), _Result("fatal: not a git repository", 1))
    capture = AsyncMock()
    with (
        patch.object(refresh, "_create_builder_sandbox", AsyncMock(return_value=backend)),
        patch.object(refresh, "_release_builder_sandbox", AsyncMock()),
        patch.object(refresh, "capture_workspace_snapshot", capture),
    ):
        await WORKSPACES.create(
            WorkspaceCreate(
                inherit_default_sandbox=False,
                name="base",
                repos=["acme/base"],
                setup_script="make setup",
                update_script="git pull",
            ),
            "ramon",
        )
        result = await refresh.refresh_workspace("base")
        record = await WORKSPACES.get("base")

    assert result["status"] == "failed"
    assert result["script"] == "update"
    capture.assert_not_awaited()
    assert _scripts_run(backend) == ["make setup", "git pull"]
    assert record is not None
    assert record.refresh_error == "update script exited 1"
    # Both sections ride along, so the model can see what ran before the break.
    assert record.refresh_log is not None
    assert "--- setup script ---" in record.refresh_log
    assert "fatal: not a git repository" in record.refresh_log


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
async def test_a_failing_script_is_never_captured() -> None:
    backend = _backend(_Result("gcc: fatal error", 2))
    capture = AsyncMock()
    with (
        patch.object(refresh, "_create_builder_sandbox", AsyncMock(return_value=backend)),
        patch.object(refresh, "_release_builder_sandbox", AsyncMock()),
        patch.object(refresh, "capture_workspace_snapshot", capture),
    ):
        await WORKSPACES.create(
            WorkspaceCreate(
                inherit_default_sandbox=False,
                name="base",
                repos=["acme/base"],
                setup_script="make setup",
            ),
            "ramon",
        )
        # A snapshot from an earlier refresh; runs must keep booting from it.
        await WORKSPACES.mark_captured(
            "base",
            snapshot_id="snap-1",
            snapshot_name="openswe-environment-base",
            source_sandbox_id="sb-prior",
        )
        result = await refresh.refresh_workspace("base")
        record = await WORKSPACES.get("base")

    assert result["status"] == "failed"
    capture.assert_not_awaited()
    assert record is not None
    assert record.refresh_status == "failed"
    assert record.refresh_error == "setup script exited 2"
    assert record.ready_snapshot_id == "snap-1"


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
async def test_a_sandbox_that_never_boots_still_records_the_failure() -> None:
    release = AsyncMock()
    with (
        patch.object(
            refresh,
            "_create_builder_sandbox",
            AsyncMock(side_effect=RuntimeError("no capacity")),
        ),
        patch.object(refresh, "_release_builder_sandbox", release),
    ):
        await WORKSPACES.create(
            WorkspaceCreate(
                inherit_default_sandbox=False,
                name="base",
                repos=["acme/base"],
                setup_script="make setup",
            ),
            "ramon",
        )
        result = await refresh.refresh_workspace("base")
        record = await WORKSPACES.get("base")

    assert result["status"] == "failed"
    assert result["error"] == "no capacity"
    release.assert_not_awaited()
    assert record is not None
    assert record.refresh_error == "no capacity"


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
async def test_a_refresh_in_flight_blocks_a_second_one() -> None:
    create = AsyncMock()
    with patch.object(refresh, "_create_builder_sandbox", create):
        await WORKSPACES.create(
            WorkspaceCreate(
                inherit_default_sandbox=False,
                name="base",
                repos=["acme/base"],
                setup_script="make setup",
            ),
            "ramon",
        )
        await WORKSPACES.mark_refreshing("base")
        result = await refresh.refresh_workspace("base")

    assert result["status"] == "already_refreshing"
    create.assert_not_awaited()


# --- update kind + lazy trigger ---


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
async def test_an_update_boots_from_the_current_snapshot_and_runs_only_the_update_script() -> None:
    backend = _backend(_Result("Already up to date.", 0))
    create_builder = AsyncMock(return_value=backend)
    capture = AsyncMock()
    with (
        patch.object(refresh, "_create_builder_sandbox", create_builder),
        patch.object(refresh, "_release_builder_sandbox", AsyncMock()),
        patch.object(refresh, "capture_workspace_snapshot", capture),
    ):
        await WORKSPACES.create(
            WorkspaceCreate(
                inherit_default_sandbox=False,
                name="base",
                repos=["acme/base"],
                setup_script="make setup",
                update_script="git pull",
            ),
            "ramon",
        )
        await WORKSPACES.mark_captured(
            "base",
            snapshot_id="snap-1",
            snapshot_name="openswe-environment-base",
            source_sandbox_id="sb-prior",
        )
        result = await refresh.refresh_workspace("base", "update")
        record = await WORKSPACES.get("base")

    assert result["status"] == "success"
    assert result["kind"] == "update"
    # From the live snapshot, not the base image.
    assert create_builder.await_args is not None
    assert create_builder.await_args.args[1] == "snap-1"
    assert _scripts_run(backend) == ["git pull"]
    capture.assert_awaited_once()
    assert record is not None
    assert record.refresh_kind == "update"


# --- cron ---


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
async def test_the_step_that_broke_is_the_one_left_failed() -> None:
    backend = _backend(_Result("gcc: fatal error", 2))
    with (
        patch.object(refresh, "_create_builder_sandbox", AsyncMock(return_value=backend)),
        patch.object(refresh, "_release_builder_sandbox", AsyncMock()),
        patch.object(refresh, "capture_workspace_snapshot", AsyncMock()),
    ):
        await WORKSPACES.create(
            WorkspaceCreate(
                inherit_default_sandbox=False,
                name="base",
                repos=["acme/base"],
                setup_script="make setup",
            ),
            "ramon",
        )
        await refresh.refresh_workspace("base")
        record = await WORKSPACES.get("base")

    assert record is not None
    assert [(step.label, step.status, step.exit_code) for step in record.refresh_steps] == [
        ("boot", "success", None),
        ("setup", "failed", 2),
    ]


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
async def test_the_builder_is_published_while_it_lives_and_cleared_after() -> None:
    """A poll reads the running trace off the builder, so its id must be current."""
    backend = _backend(_Result("provisioned", 0))
    published: list[str | None] = []

    async def _capture(slug: str, sandbox_id: str, **_: object) -> None:
        record = await WORKSPACES.get(slug)
        published.append(record.refresh_sandbox_id if record else None)

    with (
        patch.object(refresh, "_create_builder_sandbox", AsyncMock(return_value=backend)),
        patch.object(refresh, "_release_builder_sandbox", AsyncMock()),
        patch.object(refresh, "capture_workspace_snapshot", _capture),
    ):
        await WORKSPACES.create(
            WorkspaceCreate(
                inherit_default_sandbox=False,
                name="base",
                repos=["acme/base"],
                setup_script="make setup",
            ),
            "ramon",
        )
        await refresh.refresh_workspace("base")
        record = await WORKSPACES.get("base")

    assert published == ["sb-builder"]
    assert record is not None
    # Released with the refresh, so it stops being offered as a readable source.
    assert record.refresh_sandbox_id is None
