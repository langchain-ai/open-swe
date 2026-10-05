"""The update script that a run's own sandbox executes when its image is stale."""

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.sandboxes.lifecycle import SandboxCreateConfig
from agent.workspaces.store import Workspace, script_log_path


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


def _stale(**overrides: object) -> Workspace:
    """A workspace whose snapshot has never been captured, so it is stale."""
    return Workspace(
        slug="base",
        update_script="git pull",
        snapshot_status="ready",
        snapshot_id="snap-1",
    ).model_copy(update=overrides)


@pytest.mark.asyncio
async def test_a_stale_image_is_freshened_before_the_run_starts() -> None:
    backend = _backend(_Result("Already up to date.", 0))
    config = SandboxCreateConfig(snapshot_id="snap-1", workspace=_stale())

    await config.run_update_script(backend, "t-1")

    assert _script_run(backend) == "git pull"
    # Traced, and logged where the snapshot will carry it.
    assert "bash -x " in _command(backend)
    assert script_log_path("update") in _command(backend)


@pytest.mark.asyncio
async def test_an_execute_that_raises_does_not_lose_the_sandbox() -> None:
    """`aexecute` can raise past its retries; the box is still usable without a pull."""
    backend = MagicMock()
    backend.id = "sb-1"
    backend.aexecute = AsyncMock(side_effect=RuntimeError("sandbox unreachable"))

    await SandboxCreateConfig(snapshot_id="snap-1", workspace=_stale()).run_update_script(
        backend, "t-1"
    )

    backend.aexecute.assert_awaited_once()


@pytest.mark.asyncio
async def test_inherited_sandbox_tracks_default_without_changing_workspace_identity(
    monkeypatch,
) -> None:
    child = Workspace(slug="child", inherit_default_sandbox=True, prompt="Child instructions")
    default = _stale(
        slug="default", mem_bytes=1234, create_params={"preserve_memory_on_stop": True}
    )

    async def load(slug: str | None) -> Workspace:
        return child if slug == "child" else default

    monkeypatch.setattr("agent.sandboxes.lifecycle.load_workspace", load)
    config = await SandboxCreateConfig.resolve("child")
    assert config.snapshot_id == "snap-1"
    assert config.resources == {"mem_bytes": 1234}
    assert config.create_params == {"preserve_memory_on_stop": True}
    assert child.prompt == "Child instructions"
    default.snapshot_id = "snap-2"
    assert (await SandboxCreateConfig.resolve("child")).snapshot_id == "snap-2"
    child.inherit_default_sandbox = False
    assert (await SandboxCreateConfig.resolve("child")).snapshot_id is None
    assert (await SandboxCreateConfig.resolve("child", source="base")).snapshot_id is None
