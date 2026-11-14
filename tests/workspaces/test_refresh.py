import asyncio
import base64
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from openswe.database import transaction
from openswe.webhooks import event_log
from openswe.workspaces import refresh
from openswe.workspaces.store import WORKSPACES, Workspace, WorkspaceCreate


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
@pytest.mark.parametrize(
    "capture_error",
    [None, TimeoutError(), RuntimeError("capture failed with opaque-build-credential")],
)
async def test_refresh_history_keeps_outcomes_without_raw_diagnostics(
    capture_error: Exception | None,
) -> None:
    """Historical events must not copy arbitrary credentials from logs or exception messages."""
    backend = _backend(_Result("Updating repo with opaque-build-credential", 0))
    with (
        patch.object(refresh, "_create_builder_sandbox", AsyncMock(return_value=backend)),
        patch.object(refresh, "_release_builder_sandbox", AsyncMock()),
        patch.object(refresh, "capture_workspace_snapshot", AsyncMock(side_effect=capture_error)),
        patch.object(event_log, "_ROTATED_AT", None),
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
    status = "failed" if capture_error is not None else "success"
    expected_error = (str(capture_error) or type(capture_error).__name__) if capture_error else None
    assert record.refresh_error == expected_error
    assert record.refresh_log is not None
    assert "opaque-build-credential" in record.refresh_log
    async with transaction() as conn:
        rows = await conn.execute(
            text(
                "SELECT event_type, payload, workspace_id FROM event_log WHERE source = 'workspace'"
            )
        )
        [(event_type, payload, workspace_id)] = rows.tuples().all()
    assert event_type == f"refresh.{status}"
    assert workspace_id == await WORKSPACES.id_for_slug("base")
    assert "error" not in payload
    assert "log_tail" not in payload
    assert "opaque-build-credential" not in str(payload)
    assert [(step["label"], step["status"]) for step in payload["steps"]] == [
        ("boot", "success"),
        ("setup", "success"),
        ("capture", status),
    ]


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
async def test_a_refresh_in_flight_blocks_a_second_one_but_not_its_own_retry() -> None:
    """A job retried after a redeploy cut it short must not wait hours behind itself."""
    create = AsyncMock(return_value=_backend(_Result("provisioned", 0)))
    release = AsyncMock()
    with (
        patch.object(refresh, "_create_builder_sandbox", create),
        patch.object(refresh, "_release_builder_sandbox", release),
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
        original = await WORKSPACES.mark_refreshing("base", run_id="run-a")
        assert original is not None
        await original.publish_builder("sb-lost")
        other = await refresh.refresh_workspace("base", run_id="run-b")
        create.assert_not_awaited()
        retried = await refresh.refresh_workspace("base", run_id="run-a")
        # A stalled original that outlived its lease can no longer settle over the retry.
        await original.settle("failed", error="builder stopped")
        record = await WORKSPACES.get("base")

    assert other["status"] == "already_refreshing"
    assert retried["status"] == "success"
    assert record is not None
    assert record.refresh_status == "success"
    assert record.refresh_error is None
    # The cut-short attempt's builder is stopped, not leaked.
    assert [call.args[0] for call in release.await_args_list] == ["sb-lost", "sb-builder"]


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
async def test_a_queued_job_never_takes_over_a_cron_refresh_that_claimed_first() -> None:
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
        # Queueing a job writes its id before the job runs; the nightly cron claims meanwhile.
        await WORKSPACES.set_refresh_run_id("base", "job-y")
        assert await WORKSPACES.mark_refreshing("base") is not None
        result = await refresh.refresh_workspace("base", run_id="job-y")

    assert result["status"] == "already_refreshing"
    create.assert_not_awaited()


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
async def test_a_cancelled_refresh_is_settled_so_its_retry_can_run() -> None:
    backend = _backend()
    backend.aexecute = AsyncMock(side_effect=asyncio.CancelledError)
    release = AsyncMock()
    with (
        patch.object(refresh, "_create_builder_sandbox", AsyncMock(return_value=backend)),
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
        with pytest.raises(asyncio.CancelledError):
            await refresh.refresh_workspace("base", run_id="run-a")
        record = await WORKSPACES.get("base")

    release.assert_awaited_once_with("sb-builder")
    assert record is not None
    assert record.refresh_status == "failed"
    assert record.refresh_error == "interrupted"
    assert not record.refresh_in_flight()


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


class TestFailedUpdateRecovery:
    @pytest.mark.usefixtures("registry_db")
    @pytest.mark.parametrize("failure_at", ["script", "boot", "capture"])
    async def test_failed_update_queues_one_full_rebuild(
        self, failure_at: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        backend = _backend(_Result("update output", 1 if failure_at == "script" else 0))
        create = AsyncMock(return_value=backend)
        capture = AsyncMock()
        if failure_at == "boot":
            create.side_effect = RuntimeError("no capacity")
        if failure_at == "capture":
            capture.side_effect = TimeoutError("capture timed out")
        with (
            patch.object(refresh, "_create_builder_sandbox", create),
            patch.object(refresh, "_release_builder_sandbox", AsyncMock()),
            patch.object(refresh, "capture_workspace_snapshot", capture),
            patch.object(
                refresh.queue, "schedule", AsyncMock(return_value=MagicMock(id="full-job"))
            ) as schedule,
        ):
            await WORKSPACES.create(
                WorkspaceCreate(
                    inherit_default_sandbox=False,
                    name="base",
                    setup_script="make setup",
                    update_script="git pull",
                ),
                "ramon",
            )
            await WORKSPACES.mark_captured(
                "base", snapshot_id="snap-1", snapshot_name="base", source_sandbox_id="sb-prior"
            )
            result = await refresh.refresh_workspace("base", "update", run_id="update-job")
            record = await WORKSPACES.get("base")
            assert record is not None
            assert record.refresh_status == "failed"
            assert record.refresh_error == result["error"]
            assert record.ready_snapshot_id == "snap-1"
            assert record.refresh_run_id == "full-job"
            assert not refresh.is_update_due(record)
            assert schedule.await_count == 1
            assert schedule.await_args is not None
            assert schedule.await_args.args[1] == {"workspace_slug": "base", "refresh_kind": "full"}
            backend.aexecute.side_effect = [_Result("full rebuild failed", 1)]
            rebuilt = await refresh.refresh_workspace("base", "full", run_id="full-job")
            assert rebuilt["status"] == "failed"
            assert schedule.await_count == 1
        assert result["status"] == "failed"
        assert any(
            entry.levelno == logging.ERROR
            and entry.__dict__.get("workspace") == "base"
            and entry.__dict__.get("refresh_run_id") == "update-job"
            for entry in caplog.records
        )

    @pytest.mark.usefixtures("registry_db")
    @pytest.mark.parametrize("fallback", ["unsupported", "missing_run", "queue_exception"])
    async def test_unavailable_fallback_preserves_the_failed_update(
        self, fallback: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        with (
            patch.object(
                refresh,
                "_create_builder_sandbox",
                AsyncMock(return_value=_backend(_Result("failed", 1))),
            ),
            patch.object(refresh, "_release_builder_sandbox", AsyncMock()),
            patch.object(
                refresh,
                "start_refresh_run",
                AsyncMock(
                    return_value=None,
                    side_effect=RuntimeError("queue unavailable")
                    if fallback == "queue_exception"
                    else None,
                ),
            ),
        ):
            await WORKSPACES.create(
                WorkspaceCreate(
                    inherit_default_sandbox=False,
                    name="base",
                    setup_script="" if fallback == "unsupported" else "make setup",
                    update_script="git pull",
                ),
                "ramon",
            )
            await WORKSPACES.mark_captured(
                "base", snapshot_id="snap-1", snapshot_name="base", source_sandbox_id="sb-prior"
            )
            result = await refresh.refresh_workspace("base", "update", run_id="update-job")
            record = await WORKSPACES.get("base")
        assert result["status"] == "failed"
        assert record is not None
        assert record.refresh_status == "failed"
        assert record.refresh_error == "update script exited 1"
        assert record.ready_snapshot_id == "snap-1"
        assert not refresh.is_update_due(record)
        assert "Could not queue full refresh after failed update" in caplog.text
        assert "Queued full refresh after failed update" not in caplog.text

    @pytest.mark.parametrize("queue_error", [None, RuntimeError("queue unavailable")])
    async def test_missing_update_run_is_reported_as_a_queue_failure(
        self, queue_error: Exception | None, caplog: pytest.LogCaptureFixture
    ) -> None:
        record = Workspace(
            slug="base",
            name="base",
            update_script="git pull",
            snapshot_id="snap-1",
            snapshot_status="ready",
            last_captured_at=(datetime.now(UTC) - timedelta(hours=2)).isoformat(),
        )
        with patch.object(
            refresh, "start_refresh_run", AsyncMock(return_value=None, side_effect=queue_error)
        ):
            assert await refresh.maybe_start_update(record) is None
        assert any(entry.levelno == logging.ERROR for entry in caplog.records)
        assert "Queued workspace update" not in caplog.text
