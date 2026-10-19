"""Which workspace image a new sandbox boots from."""

import pytest

from openswe.sandboxes.lifecycle import SandboxCreateConfig
from openswe.workspaces.store import Workspace


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
        create_params={"preserve_memory_on_stop": False},
    )

    async def load(slug: str | None) -> Workspace:
        return child if slug == "child" else default

    monkeypatch.setattr("openswe.sandboxes.lifecycle.load_workspace", load)
    config = await SandboxCreateConfig.resolve("child")
    assert config.snapshot_id == "snap-1"
    assert config.resources == {"mem_bytes": 1234}
    assert config.create_params == {"preserve_memory_on_stop": True}
    assert default.create_params == {"preserve_memory_on_stop": False}
    assert child.prompt == "Child instructions"
    default.snapshot_id = "snap-2"
    assert (await SandboxCreateConfig.resolve("child")).snapshot_id == "snap-2"
    child.inherit_default_sandbox = False
    assert (await SandboxCreateConfig.resolve("child")).snapshot_id is None
    assert (await SandboxCreateConfig.resolve("child", source="base")).snapshot_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "declared"),
    [
        ({}, "open-swe-thread-1"),
        ({"sandbox_id": "box-from-backend"}, "box-from-backend"),
        ({"sandbox_id": "bridge:abc"}, None),
        ({"task_id": "00000000-0000-0000-0000-000000000001"}, None),
    ],
)
async def test_mda_declares_the_box_the_thread_is_bound_to(
    monkeypatch, metadata: dict[str, str], declared: str | None
) -> None:
    """MDA adopts the thread's box by name; a new thread gets its own, bound when MDA made it."""
    from openswe.sandboxes.managed import ManagedSandbox

    workspace = Workspace(
        slug="default",
        snapshot_status="ready",
        snapshot_id="snap-1",
        create_params={"proxy_config": {"rules": []}, "preserve_memory_on_stop": False},
    )
    updates: list[dict[str, object]] = []

    async def load(slug: str | None) -> Workspace:
        return workspace

    async def read_metadata(thread_id: str) -> dict[str, str]:
        return {**metadata, **(updates[-1] if updates else {})}

    async def update(*, thread_id: str, metadata: dict[str, object]) -> None:
        updates.append(metadata)

    monkeypatch.setenv("SANDBOX_TYPE", "langsmith")
    monkeypatch.setattr("openswe.sandboxes.lifecycle.load_workspace", load)
    monkeypatch.setattr("openswe.sandboxes.managed.get_sandbox_metadata", read_metadata)
    monkeypatch.setattr("openswe.sandboxes.lifecycle.get_sandbox_metadata", read_metadata)
    monkeypatch.setattr("openswe.sandboxes.lifecycle.client.threads.update", update)
    monkeypatch.setattr("openswe.sandboxes.lifecycle.maybe_start_update", _no_update)

    sandbox = await ManagedSandbox.for_thread("thread-1", None)

    assert (sandbox.name if sandbox else None) == declared
    if sandbox is None:
        return
    assert sandbox.create.snapshot_id == "snap-1"
    # The backend sets the proxy when it connects; MDA leaves it alone.
    assert sandbox.create.extra_fields == {"preserve_memory_on_stop": True}
    await sandbox.bind()
    await sandbox.bind()
    expected = (
        []
        if "sandbox_id" in metadata
        else [{"sandbox_id": declared, "sandbox_base_proxy_config": {"rules": []}}]
    )
    assert updates == expected


async def _no_update(workspace: object) -> None:
    return None
