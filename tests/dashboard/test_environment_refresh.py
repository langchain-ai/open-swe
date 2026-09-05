import base64
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
    command = env_store.script_command(script, env_store.SETUP_SCRIPT_PATH)

    encoded = command.split("printf %s ")[1].split(" |")[0].strip("'")
    assert base64.b64decode(encoded).decode() == script
    assert command.endswith(f"bash {env_store.SETUP_SCRIPT_PATH} 2>&1")


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
async def test_the_init_script_runs_after_setup_and_gates_the_capture(
    fake_store: FakeStore,
) -> None:
    """A broken init script must be caught here, not on someone's first run."""
    backend = _backend(_Result("provisioned", 0), _Result("fatal: not a git repository", 1))
    capture = AsyncMock()
    with (
        patch.object(refresh, "_create_builder_sandbox", AsyncMock(return_value=backend)),
        patch.object(refresh, "_release_builder_sandbox", AsyncMock()),
        patch.object(refresh, "capture_environment_snapshot", capture),
    ):
        await ENVIRONMENTS.create(
            EnvironmentCreate(name="base", setup_script="make setup", init_script="git pull"),
            "ramon",
        )
        result = await refresh.refresh_environment("base")
        record = await ENVIRONMENTS.get("base")

    assert result["status"] == "failed"
    assert result["script"] == "init"
    capture.assert_not_awaited()
    assert _scripts_run(backend) == ["make setup", "git pull"]
    assert record is not None
    assert record.refresh_error == "init script exited 1"
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
