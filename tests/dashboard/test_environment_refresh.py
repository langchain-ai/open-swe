import base64
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.dashboard import environment_refresh as refresh
from agent.dashboard import environments as env_store
from agent.dashboard.environments import ENVIRONMENTS, Environment, EnvironmentCreate
from tests.conftest import FakeStore


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


def test_script_command_carries_the_script_verbatim() -> None:
    """Base64 so quotes and heredocs in the body cannot break the command."""
    script = "set -euo pipefail\ngit clone 'git@github.com:acme/repo'  # it's fine\n"
    command = env_store.script_command(script, "setup")

    encoded = command.split("printf %s ")[1].split(" |")[0].strip("'")
    assert base64.b64decode(encoded).decode() == script


def test_script_command_traces_into_a_canonical_log_and_keeps_the_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The log rides into the snapshot, and `cat` must not mask a failing script."""
    monkeypatch.delenv("OPENSWE_SCRIPT_ROOT", raising=False)
    command = env_store.script_command("git pull", "update")

    assert env_store.script_log_path("update") == "/open-swe/environment/logs/update.log"
    assert "mkdir -p /open-swe/environment/logs" in command
    assert (
        "bash -x /open-swe/environment/update.sh > /open-swe/environment/logs/update.log" in command
    )
    # The script's own status, captured before `cat` runs.
    assert "rc=$?" in command
    assert command.endswith("exit $rc")


def test_the_script_root_is_configurable_for_providers_without_a_writable_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`SANDBOX_TYPE=local` runs on a developer's machine, where / is read-only."""
    monkeypatch.setenv("OPENSWE_SCRIPT_ROOT", "/tmp/e2e/open-swe/environment/")

    assert env_store.script_root() == "/tmp/e2e/open-swe/environment"
    assert env_store.script_log_path("setup") == "/tmp/e2e/open-swe/environment/logs/setup.log"
    assert "mkdir -p /tmp/e2e/open-swe/environment/logs" in env_store.script_command(
        "make setup", "setup"
    )


def test_daily_schedule_is_stable_and_staggered() -> None:
    assert refresh.daily_schedule("default") == refresh.daily_schedule("default")
    schedules = {refresh.daily_schedule(slug) for slug in ("default", "staging", "preview")}
    assert len(schedules) > 1
    for schedule in schedules:
        minute, hour, *rest = schedule.split()
        assert 0 <= int(minute) < 60
        assert 3 <= int(hour) < 6
        assert rest == ["*", "*", "*"]


def test_a_wedged_refresh_does_not_block_forever() -> None:
    stale = Environment(slug="base", refresh_status="refreshing", refresh_started_at="2020-01-01")
    assert refresh.is_refresh_in_flight(stale) is False
    assert refresh.is_refresh_in_flight(Environment(slug="base")) is False


# --- refresh ---


@pytest.mark.asyncio
async def test_refresh_runs_the_script_then_captures_and_cleans_up(
    fake_store: FakeStore,
) -> None:
    backend = _backend(_Result("cloning acme/repo\ndone", 0))
    capture = AsyncMock()
    release = AsyncMock()
    with (
        patch.object(refresh, "_create_builder_sandbox", AsyncMock(return_value=backend)),
        patch.object(refresh, "_release_builder_sandbox", release),
        patch.object(refresh, "capture_environment_snapshot", capture),
    ):
        await ENVIRONMENTS.create(
            EnvironmentCreate(name="base", setup_script="make setup"), "ramon"
        )
        result = await refresh.refresh_environment("base")
        record = await ENVIRONMENTS.get("base")

    assert result["status"] == "success"
    assert _scripts_run(backend) == ["make setup"]
    capture.assert_awaited_once()
    assert capture.await_args is not None
    assert capture.await_args.args[:2] == ("base", "sb-builder")
    release.assert_awaited_once_with("sb-builder")
    assert record is not None
    assert record.refresh_status == "success"
    assert record.refresh_log == "--- setup script ---\ncloning acme/repo\ndone"
    assert record.refresh_error is None
    assert record.refresh_finished_at


@pytest.mark.asyncio
async def test_the_update_script_runs_after_setup_and_gates_the_capture(
    fake_store: FakeStore,
) -> None:
    """A broken update script must be caught here, not by the next hourly update."""
    backend = _backend(_Result("provisioned", 0), _Result("fatal: not a git repository", 1))
    capture = AsyncMock()
    with (
        patch.object(refresh, "_create_builder_sandbox", AsyncMock(return_value=backend)),
        patch.object(refresh, "_release_builder_sandbox", AsyncMock()),
        patch.object(refresh, "capture_environment_snapshot", capture),
    ):
        await ENVIRONMENTS.create(
            EnvironmentCreate(name="base", setup_script="make setup", update_script="git pull"),
            "ramon",
        )
        result = await refresh.refresh_environment("base")
        record = await ENVIRONMENTS.get("base")

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


@pytest.mark.asyncio
async def test_a_failing_script_is_never_captured(fake_store: FakeStore) -> None:
    backend = _backend(_Result("gcc: fatal error", 2))
    capture = AsyncMock()
    with (
        patch.object(refresh, "_create_builder_sandbox", AsyncMock(return_value=backend)),
        patch.object(refresh, "_release_builder_sandbox", AsyncMock()),
        patch.object(refresh, "capture_environment_snapshot", capture),
    ):
        await ENVIRONMENTS.create(
            EnvironmentCreate(name="base", setup_script="make setup"), "ramon"
        )
        # A snapshot from an earlier refresh; runs must keep booting from it.
        await ENVIRONMENTS.mark_captured(
            "base",
            snapshot_id="snap-1",
            snapshot_name="openswe-environment-base",
            source_sandbox_id="sb-prior",
        )
        result = await refresh.refresh_environment("base")
        record = await ENVIRONMENTS.get("base")

    assert result["status"] == "failed"
    capture.assert_not_awaited()
    assert record is not None
    assert record.refresh_status == "failed"
    assert record.refresh_error == "setup script exited 2"
    assert record.ready_snapshot_id == "snap-1"


@pytest.mark.asyncio
async def test_a_sandbox_that_never_boots_still_records_the_failure(
    fake_store: FakeStore,
) -> None:
    release = AsyncMock()
    with (
        patch.object(
            refresh,
            "_create_builder_sandbox",
            AsyncMock(side_effect=RuntimeError("no capacity")),
        ),
        patch.object(refresh, "_release_builder_sandbox", release),
    ):
        await ENVIRONMENTS.create(
            EnvironmentCreate(name="base", setup_script="make setup"), "ramon"
        )
        result = await refresh.refresh_environment("base")
        record = await ENVIRONMENTS.get("base")

    assert result["status"] == "failed"
    assert result["error"] == "no capacity"
    release.assert_not_awaited()
    assert record is not None
    assert record.refresh_error == "no capacity"


@pytest.mark.asyncio
async def test_an_environment_without_a_script_is_not_refreshed(fake_store: FakeStore) -> None:
    create = AsyncMock()
    with patch.object(refresh, "_create_builder_sandbox", create):
        await ENVIRONMENTS.create(EnvironmentCreate(name="base"), "ramon")
        result = await refresh.refresh_environment("base")

    assert result["status"] == "no_setup_script"
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_refresh_in_flight_blocks_a_second_one(fake_store: FakeStore) -> None:
    create = AsyncMock()
    with patch.object(refresh, "_create_builder_sandbox", create):
        await ENVIRONMENTS.create(
            EnvironmentCreate(name="base", setup_script="make setup"), "ramon"
        )
        await ENVIRONMENTS.mark_refreshing("base")
        result = await refresh.refresh_environment("base")

    assert result["status"] == "already_refreshing"
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_requires_the_langsmith_provider(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "local")
    create = AsyncMock()
    with patch.object(refresh, "_create_builder_sandbox", create):
        await ENVIRONMENTS.create(
            EnvironmentCreate(name="base", setup_script="make setup"), "ramon"
        )
        result = await refresh.refresh_environment("base")

    assert result["status"] == "unsupported"
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_nightly_sweep_only_visits_scripted_environments(
    fake_store: FakeStore,
) -> None:
    refreshed = AsyncMock(return_value={"status": "success"})
    with patch.object(refresh, "refresh_environment", refreshed):
        await ENVIRONMENTS.create(
            EnvironmentCreate(name="scripted", setup_script="make setup"), "ramon"
        )
        await ENVIRONMENTS.create(EnvironmentCreate(name="bare"), "ramon")
        await refresh.run_environment_refresh_tick(None)

    assert [call.args[0] for call in refreshed.await_args_list] == ["scripted"]


# --- update kind + lazy trigger ---


@pytest.mark.asyncio
async def test_an_update_boots_from_the_current_snapshot_and_runs_only_the_update_script(
    fake_store: FakeStore,
) -> None:
    backend = _backend(_Result("Already up to date.", 0))
    create_builder = AsyncMock(return_value=backend)
    capture = AsyncMock()
    with (
        patch.object(refresh, "_create_builder_sandbox", create_builder),
        patch.object(refresh, "_release_builder_sandbox", AsyncMock()),
        patch.object(refresh, "capture_environment_snapshot", capture),
    ):
        await ENVIRONMENTS.create(
            EnvironmentCreate(name="base", setup_script="make setup", update_script="git pull"),
            "ramon",
        )
        await ENVIRONMENTS.mark_captured(
            "base",
            snapshot_id="snap-1",
            snapshot_name="openswe-environment-base",
            source_sandbox_id="sb-prior",
        )
        result = await refresh.refresh_environment("base", "update")
        record = await ENVIRONMENTS.get("base")

    assert result["status"] == "success"
    assert result["kind"] == "update"
    # From the live snapshot, not the base image.
    assert create_builder.await_args is not None
    assert create_builder.await_args.args[1] == "snap-1"
    assert _scripts_run(backend) == ["git pull"]
    capture.assert_awaited_once()
    assert record is not None
    assert record.refresh_kind == "update"


@pytest.mark.asyncio
async def test_an_update_needs_a_snapshot_to_update(fake_store: FakeStore) -> None:
    create_builder = AsyncMock()
    with patch.object(refresh, "_create_builder_sandbox", create_builder):
        await ENVIRONMENTS.create(
            EnvironmentCreate(name="base", setup_script="make setup", update_script="git pull"),
            "ramon",
        )
        result = await refresh.refresh_environment("base", "update")

    assert result["status"] == "no_snapshot_to_update"
    create_builder.assert_not_awaited()


def test_a_snapshot_is_stale_once_its_capture_ages_out() -> None:
    """Gates the in-sandbox update: every sandbox copies the same stale image."""
    ready = Environment(
        slug="base",
        update_script="git pull",
        snapshot_status="ready",
        snapshot_id="snap-1",
    )
    assert refresh.is_snapshot_stale(ready) is True  # never captured
    fresh = ready.model_copy(update={"last_captured_at": datetime.now(UTC).isoformat()})
    assert refresh.is_snapshot_stale(fresh) is False
    aged = ready.model_copy(
        update={
            "last_captured_at": (
                datetime.now(UTC) - timedelta(seconds=refresh.UPDATE_INTERVAL_SECONDS + 1)
            ).isoformat()
        }
    )
    assert refresh.is_snapshot_stale(aged) is True
    # A refresh already running does not stop the sandbox from freshening itself.
    assert (
        refresh.is_snapshot_stale(
            aged.model_copy(
                update={
                    "refresh_status": "refreshing",
                    "refresh_started_at": datetime.now(UTC).isoformat(),
                }
            )
        )
        is True
    )
    assert refresh.is_snapshot_stale(ready.model_copy(update={"update_script": ""})) is False
    assert (
        refresh.is_snapshot_stale(ready.model_copy(update={"snapshot_status": "failed"})) is False
    )


def test_an_update_is_due_only_when_stale_and_idle() -> None:
    ready = Environment(
        slug="base",
        update_script="git pull",
        snapshot_status="ready",
        snapshot_id="snap-1",
    )
    assert refresh.is_update_due(ready) is True  # never captured, never refreshed
    aged = (datetime.now(UTC) - timedelta(seconds=refresh.UPDATE_INTERVAL_SECONDS + 1)).isoformat()
    fresh = ready.model_copy(update={"last_captured_at": datetime.now(UTC).isoformat()})
    assert refresh.is_update_due(fresh) is False
    stale = ready.model_copy(update={"last_captured_at": aged, "refresh_finished_at": aged})
    assert refresh.is_update_due(stale) is True
    # A recent *attempt* holds off the builder even while the image is stale, so
    # a failing script cannot enqueue one per sandbox creation.
    just_tried = stale.model_copy(update={"refresh_finished_at": datetime.now(UTC).isoformat()})
    assert refresh.is_update_due(just_tried) is False
    running = stale.model_copy(
        update={
            "refresh_status": "refreshing",
            "refresh_started_at": datetime.now(UTC).isoformat(),
        }
    )
    assert refresh.is_update_due(running) is False
    assert refresh.is_update_due(ready.model_copy(update={"update_script": ""})) is False
    assert refresh.is_update_due(ready.model_copy(update={"snapshot_status": "failed"})) is False


@pytest.mark.asyncio
async def test_a_new_sandbox_starts_a_background_update_when_one_is_due() -> None:
    start = AsyncMock(return_value="run-1")
    due = Environment(
        slug="base", update_script="git pull", snapshot_status="ready", snapshot_id="s"
    )
    with patch.object(refresh, "start_refresh_run", start):
        assert await refresh.maybe_start_update(due) == "run-1"
        assert await refresh.maybe_start_update(None) is None

    start.assert_awaited_once_with("base", kind="update")


@pytest.mark.asyncio
async def test_a_failed_trigger_never_reaches_the_sandbox_creation() -> None:
    due = Environment(
        slug="base", update_script="git pull", snapshot_status="ready", snapshot_id="s"
    )
    with patch.object(refresh, "start_refresh_run", AsyncMock(side_effect=RuntimeError("down"))):
        assert await refresh.maybe_start_update(due) is None


# --- cron ---


@pytest.mark.asyncio
async def test_cron_registration_is_idempotent(fake_store: FakeStore) -> None:
    client = MagicMock()
    client.crons.create = AsyncMock(return_value={"cron_id": "cron-1"})
    with patch.object(refresh, "_client", return_value=client):
        await ENVIRONMENTS.create(
            EnvironmentCreate(name="base", setup_script="make setup"), "ramon"
        )
        assert await refresh.ensure_refresh_cron("base") == "cron-1"
        assert await refresh.ensure_refresh_cron("base") == "cron-1"

    client.crons.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_deleting_an_environment_removes_its_cron(fake_store: FakeStore) -> None:
    client = MagicMock()
    client.crons.create = AsyncMock(return_value={"cron_id": "cron-1"})
    client.crons.delete = AsyncMock()
    with (
        patch.object(refresh, "_client", return_value=client),
        patch.object(env_store, "_delete_snapshot", AsyncMock()),
    ):
        await ENVIRONMENTS.create(
            EnvironmentCreate(name="base", setup_script="make setup"), "ramon"
        )
        await refresh.ensure_refresh_cron("base")
        assert await ENVIRONMENTS.remove("base") is True

    client.crons.delete.assert_awaited_once_with("cron-1")


@pytest.mark.asyncio
async def test_a_refresh_records_every_stage_it_reaches(fake_store: FakeStore) -> None:
    """A rebuild runs for minutes to an hour; the stage list is how it is followed."""
    backend = _backend(_Result("provisioned", 0), _Result("Already up to date.", 0))
    with (
        patch.object(refresh, "_create_builder_sandbox", AsyncMock(return_value=backend)),
        patch.object(refresh, "_release_builder_sandbox", AsyncMock()),
        patch.object(refresh, "capture_environment_snapshot", AsyncMock()),
    ):
        await ENVIRONMENTS.create(
            EnvironmentCreate(name="base", setup_script="make setup", update_script="git pull"),
            "ramon",
        )
        await refresh.refresh_environment("base")
        record = await ENVIRONMENTS.get("base")

    assert record is not None
    assert [(step.label, step.status) for step in record.refresh_steps] == [
        ("boot", "success"),
        ("setup", "success"),
        ("update", "success"),
        ("capture", "success"),
    ]
    # The script steps carry where their trace was written, for a live read.
    paths = {step.label: step.log_path for step in record.refresh_steps}
    assert paths["setup"] == env_store.script_log_path("setup")
    assert paths["boot"] is None


@pytest.mark.asyncio
async def test_the_step_that_broke_is_the_one_left_failed(fake_store: FakeStore) -> None:
    backend = _backend(_Result("gcc: fatal error", 2))
    with (
        patch.object(refresh, "_create_builder_sandbox", AsyncMock(return_value=backend)),
        patch.object(refresh, "_release_builder_sandbox", AsyncMock()),
        patch.object(refresh, "capture_environment_snapshot", AsyncMock()),
    ):
        await ENVIRONMENTS.create(
            EnvironmentCreate(name="base", setup_script="make setup"), "ramon"
        )
        await refresh.refresh_environment("base")
        record = await ENVIRONMENTS.get("base")

    assert record is not None
    assert [(step.label, step.status, step.exit_code) for step in record.refresh_steps] == [
        ("boot", "success", None),
        ("setup", "failed", 2),
    ]


@pytest.mark.asyncio
async def test_the_builder_is_published_while_it_lives_and_cleared_after(
    fake_store: FakeStore,
) -> None:
    """A poll reads the running trace off the builder, so its id must be current."""
    backend = _backend(_Result("provisioned", 0))
    published: list[str | None] = []

    async def _capture(slug: str, sandbox_id: str, **_: object) -> None:
        record = await ENVIRONMENTS.get(slug)
        published.append(record.refresh_sandbox_id if record else None)

    with (
        patch.object(refresh, "_create_builder_sandbox", AsyncMock(return_value=backend)),
        patch.object(refresh, "_release_builder_sandbox", AsyncMock()),
        patch.object(refresh, "capture_environment_snapshot", _capture),
    ):
        await ENVIRONMENTS.create(
            EnvironmentCreate(name="base", setup_script="make setup"), "ramon"
        )
        await refresh.refresh_environment("base")
        record = await ENVIRONMENTS.get("base")

    assert published == ["sb-builder"]
    assert record is not None
    # Released with the refresh, so it stops being offered as a readable source.
    assert record.refresh_sandbox_id is None
