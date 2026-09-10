"""The update script that a run's own sandbox executes when its image is stale."""

import base64
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.dashboard.environments import Environment, script_log_path
from agent.sandboxes.lifecycle import SandboxCreateConfig


class _Result:
    def __init__(self, output: str, exit_code: int) -> None:
        self.output = output
        self.exit_code = exit_code


def _backend(result: _Result) -> MagicMock:
    backend = MagicMock()
    backend.id = "sb-1"
    backend.aexecute = AsyncMock(return_value=result)
    return backend


def _command(backend: MagicMock) -> str:
    return str(backend.aexecute.call_args.args[0])


def _script_run(backend: MagicMock) -> str:
    encoded = _command(backend).split("printf %s ")[1].split(" |")[0].strip("'")
    return base64.b64decode(encoded).decode()


def _stale(**overrides: object) -> Environment:
    """An environment whose snapshot has never been captured, so it is stale."""
    return Environment(
        slug="base",
        update_script="git pull",
        snapshot_status="ready",
        snapshot_id="snap-1",
    ).model_copy(update=overrides)


@pytest.mark.asyncio
async def test_a_stale_image_is_freshened_before_the_run_starts() -> None:
    backend = _backend(_Result("Already up to date.", 0))
    config = SandboxCreateConfig(snapshot_id="snap-1", environment=_stale())

    await config.run_update_script(backend, "t-1")

    assert _script_run(backend) == "git pull"
    # Traced, and logged where the snapshot will carry it.
    assert "bash -x " in _command(backend)
    assert script_log_path("update") in _command(backend)


@pytest.mark.asyncio
async def test_a_fresh_image_costs_the_run_nothing() -> None:
    """The whole point of the hourly gate: most runs skip this entirely."""
    backend = _backend(_Result("", 0))
    fresh = _stale(last_captured_at=datetime.now(UTC).isoformat())

    await SandboxCreateConfig(snapshot_id="snap-1", environment=fresh).run_update_script(
        backend, "t-1"
    )

    backend.aexecute.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_environment_and_no_script_both_skip() -> None:
    backend = _backend(_Result("", 0))

    await SandboxCreateConfig(snapshot_id=None).run_update_script(backend, None)
    await SandboxCreateConfig(
        snapshot_id="snap-1", environment=_stale(update_script="")
    ).run_update_script(backend, None)

    backend.aexecute.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failing_update_does_not_fail_the_run() -> None:
    """The image is already usable, so a broken pull costs freshness, not the run."""
    backend = _backend(_Result("fatal: not a git repository", 1))

    await SandboxCreateConfig(snapshot_id="snap-1", environment=_stale()).run_update_script(
        backend, "t-1"
    )

    backend.aexecute.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_execute_that_raises_does_not_lose_the_sandbox() -> None:
    """`aexecute` can raise past its retries; the box is still usable without a pull."""
    backend = MagicMock()
    backend.id = "sb-1"
    backend.aexecute = AsyncMock(side_effect=RuntimeError("sandbox unreachable"))

    await SandboxCreateConfig(snapshot_id="snap-1", environment=_stale()).run_update_script(
        backend, "t-1"
    )

    backend.aexecute.assert_awaited_once()
