from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openswe.threads import moves
from openswe.threads.moves import SANDBOX_MOVED_KEY, SandboxMove

ON_THE_MAC = {
    "source": "dashboard",
    "visibility": "private",
    "owner_login": "alice",
    "sandbox_id": "bridge:b1",
    "sandbox_kind": "bridge",
    "sandbox_bridge_client": "desktop",
}


async def test_moving_to_the_cloud_leaves_the_box_and_the_carry_to_mda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cloud box is MDA's to create when the thread's next run starts; the checkout
    comes over when that run connects to it."""
    stored: dict[str, object] = dict(ON_THE_MAC)

    async def update(*, thread_id: str, metadata: dict[str, object]) -> None:
        stored.update(metadata)

    client = SimpleNamespace(
        threads=SimpleNamespace(get=AsyncMock(return_value={"status": "idle"}), update=update)
    )
    connect = AsyncMock()
    monkeypatch.setattr(moves, "langgraph_client", lambda: client)
    monkeypatch.setattr(moves, "mirror_thread_metadata", AsyncMock())
    monkeypatch.setattr("openswe.sandboxes.lifecycle.ensure_sandbox_for_thread", connect)
    monkeypatch.setattr(moves.BridgeStore, "is_connected", AsyncMock(return_value=True))

    move = await SandboxMove.requested(
        "thread-1", ON_THE_MAC, login="alice", bridge=None, target="cloud"
    )
    assert move is not None
    await move.carry_out()

    connect.assert_not_awaited()
    assert stored["sandbox_id"] is None
    assert stored["sandbox_handoff_from"] == "bridge:b1"
    assert stored[SANDBOX_MOVED_KEY] == {"to_cloud": True, "carried": True}
