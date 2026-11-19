from types import SimpleNamespace

import pytest

from openswe.bridge import worktree_handoff as handoff
from openswe.sandboxes.state import SANDBOX_BACKENDS

THREAD_ID = "thread-cloud"


async def test_a_thread_in_a_cloud_sandbox_is_refused_without_connecting_to_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def sandbox_id(_thread_id: str) -> str:
        return "sandbox-1"

    async def connect(_thread_id: str) -> object:
        raise AssertionError("connected to a sandbox that is not a desktop bridge")

    SANDBOX_BACKENDS.pop(THREAD_ID, None)
    monkeypatch.setattr(
        handoff,
        "RunConfig",
        SimpleNamespace(from_runtime=lambda: SimpleNamespace(thread_id=THREAD_ID)),
    )
    monkeypatch.setattr(handoff, "get_sandbox_id_from_metadata", sandbox_id)
    monkeypatch.setattr(handoff, "get_sandbox_backend", connect)

    result = await handoff.worktree_handoff(branch="feature")

    assert result == {"success": False, "error": "This thread is not running on the desktop app."}
