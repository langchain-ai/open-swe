"""Which workspace image a new sandbox boots from."""

import pytest

from agent.sandboxes.lifecycle import SandboxCreateConfig
from agent.workspaces.store import Workspace


@pytest.mark.asyncio
async def test_inherited_sandbox_tracks_default_without_changing_workspace_identity(
    monkeypatch,
) -> None:
    child = Workspace(slug="child", inherit_default_sandbox=True, prompt="Child instructions")
    default = Workspace(
        slug="default",
        update_script="git pull",
        snapshot_status="ready",
        snapshot_id="snap-1",
        mem_bytes=1234,
        create_params={"preserve_memory_on_stop": True},
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
