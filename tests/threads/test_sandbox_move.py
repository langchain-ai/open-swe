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


@pytest.mark.parametrize("carry_fails", [False, True])
async def test_moving_to_the_cloud_carries_the_checkout_now_or_not_at_all(
    monkeypatch: pytest.MonkeyPatch, carry_fails: bool
) -> None:
    stored: dict[str, object] = dict(ON_THE_MAC)

    async def update(*, thread_id: str, metadata: dict[str, object]) -> None:
        stored.update(metadata)

    client = SimpleNamespace(
        threads=SimpleNamespace(get=AsyncMock(return_value={"status": "idle"}), update=update)
    )
    carry = AsyncMock(side_effect=RuntimeError("pack failed") if carry_fails else None)
    monkeypatch.setattr(moves, "langgraph_client", lambda: client)
    monkeypatch.setattr(moves, "mirror_thread_metadata", AsyncMock())
    monkeypatch.setattr("openswe.sandboxes.lifecycle.ensure_sandbox_for_thread", carry)
    monkeypatch.setattr(moves.BridgeStore, "is_connected", AsyncMock(return_value=True))

    move = await SandboxMove.requested(
        "thread-1", ON_THE_MAC, login="alice", bridge=None, target="cloud"
    )
    assert move is not None
    if carry_fails:
        with pytest.raises(RuntimeError, match="pack failed"):
            await move.carry_out()
        assert stored["sandbox_id"] == "bridge:b1"
        assert stored["sandbox_handoff_from"] is None
        assert stored[SANDBOX_MOVED_KEY] is None
    else:
        await move.carry_out()
        assert stored["sandbox_id"] is None
        assert stored["sandbox_handoff_from"] == "bridge:b1"
        assert stored[SANDBOX_MOVED_KEY] == {"to_cloud": True, "carried": True}
    carry.assert_awaited_once_with("thread-1", workspace_slug=None)
