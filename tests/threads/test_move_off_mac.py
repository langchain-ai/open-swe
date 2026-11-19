from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from openswe.prompt import TurnContext
from openswe.sandboxes.this_mac import MOVED_OFF_MAC_KEY
from openswe.threads.index import IndexedThread
from openswe.threads.start_request import RunRefused
from openswe.threads.store import THREADS


def _thread(**metadata: object) -> IndexedThread:
    now = datetime.now(UTC)
    return IndexedThread.model_validate(
        {
            "thread_id": "thread-1",
            "runtime": "mda",
            "metadata": {"sandbox_id": "bridge:abc", "sandbox_kind": "bridge", **metadata},
            "status": "idle",
            "created_at": now,
            "updated_at": now,
        }
    )


async def test_a_this_mac_thread_moves_to_a_new_cloud_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    moved = _thread(sandbox_id=None, sandbox_kind=None, **{MOVED_OFF_MAC_KEY: True})
    update = AsyncMock(return_value=moved)
    monkeypatch.setattr(THREADS, "update", update)

    assert await THREADS.move_off_mac(_thread()) is moved

    update.assert_awaited_once_with(
        "thread-1",
        {
            "sandbox_id": None,
            "sandbox_base_proxy_config": None,
            "sandbox_kind": None,
            "sandbox_bridge_client": None,
            MOVED_OFF_MAC_KEY: True,
        },
    )
    assert not moved.on_a_mac


async def test_a_this_mac_task_worker_is_still_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    update = AsyncMock()
    monkeypatch.setattr(THREADS, "update", update)

    with pytest.raises(RunRefused) as refused:
        await THREADS.move_off_mac(_thread(task_id="task-1"))

    assert refused.value.status_code == 410
    update.assert_not_awaited()


def test_the_agent_is_told_the_thread_left_its_owners_machine() -> None:
    moved = TurnContext(working_dir="/work", moved_off_mac=True).environment()
    stayed = TurnContext(working_dir="/work").environment()

    assert "Nothing from that machine came over" in moved
    assert "Nothing from that machine came over" not in stayed
