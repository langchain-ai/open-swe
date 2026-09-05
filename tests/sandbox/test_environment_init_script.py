"""The per-sandbox init script that runs after a new box boots."""

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.dashboard.environments import Environment
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


def _script_run(backend: MagicMock) -> str:
    command = str(backend.aexecute.call_args.args[0])
    encoded = command.split("printf %s ")[1].split(" |")[0].strip("'")
    return base64.b64decode(encoded).decode()


@pytest.mark.asyncio
async def test_the_validated_script_runs_not_the_desired_one() -> None:
    """A save whose refresh failed leaves a broken init_script on the record."""
    backend = _backend(_Result("", 0))
    config = SandboxCreateConfig(
        snapshot_id="snap-1",
        environment=Environment(
            slug="base",
            init_script="exit 1  # saved, never validated",
            validated_init_script="git pull",
        ),
    )

    await config.run_init_script(backend, "t-1")

    assert _script_run(backend) == "git pull"


@pytest.mark.asyncio
async def test_nothing_runs_before_a_script_has_shipped() -> None:
    backend = _backend(_Result("", 0))
    config = SandboxCreateConfig(
        snapshot_id="snap-1",
        environment=Environment(slug="base", init_script="git pull"),
    )

    await config.run_init_script(backend, "t-1")

    backend.aexecute.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_environment_means_no_init_step() -> None:
    backend = _backend(_Result("", 0))

    await SandboxCreateConfig(snapshot_id=None).run_init_script(backend, None)

    backend.aexecute.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failing_init_script_does_not_fail_the_run() -> None:
    """The snapshot is already usable, so this degrades freshness, not the run."""
    backend = _backend(_Result("fatal: not a git repository", 1))
    config = SandboxCreateConfig(
        snapshot_id="snap-1",
        environment=Environment(slug="base", validated_init_script="git pull"),
    )

    await config.run_init_script(backend, "t-1")

    backend.aexecute.assert_awaited_once()
