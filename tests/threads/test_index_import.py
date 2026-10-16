import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from openswe.threads import index, index_import
from tests.conftest import MemoryEventClaims

pytestmark = pytest.mark.usefixtures("registry_db", "memory_event_claims")

_START = datetime(2026, 10, 1, tzinfo=UTC)


class _Threads:
    def __init__(self, threads: list[dict[str, Any]]) -> None:
        self.threads = threads
        self.killed_at: int | None = None

    async def search(self, *, limit: int, offset: int, **_kwargs: Any) -> list[dict[str, Any]]:
        if offset == self.killed_at:
            raise asyncio.CancelledError
        return self.threads[offset : offset + limit]


class _Local:
    def __init__(self, threads: _Threads) -> None:
        self.client = type("Client", (), {"threads": threads})()


class _Runtimes:
    def __init__(self, threads: _Threads) -> None:
        self.local = _Local(threads)


def _thread(n: int, *, updated: datetime, **metadata: Any) -> dict[str, Any]:
    return {
        "thread_id": f"t{n}",
        "metadata": metadata,
        "status": "busy" if n == 0 else "idle",
        "created_at": (_START + timedelta(minutes=n)).isoformat(),
        "updated_at": updated.isoformat(),
    }


async def test_the_index_catches_up_with_the_agent_server_after_the_switch_was_off(
    monkeypatch: pytest.MonkeyPatch, memory_event_claims: MemoryEventClaims
) -> None:
    monkeypatch.setattr(index_import, "_PAGE", 2)
    threads = [_thread(n, updated=_START, owner_login="ada") for n in range(5)]
    threads[1]["metadata"]["runtime"] = "mda"
    monkeypatch.setattr(index_import, "RUNTIMES", _Runtimes(_Threads(threads)))

    assert await index_import.import_agent_server_threads() == 5
    first, claimed = await index.get("t0"), await index.get("t1")
    assert first is not None and first.status == "busy"
    assert claimed is not None and claimed.runtime == "mda"
    # Current: nothing to do until a start with the switch off marks it stale.
    assert await index_import.import_agent_server_threads() == 0

    await index.update("t2", {"title": "renamed here"})
    threads[3] = _thread(3, updated=datetime.now(UTC) + timedelta(hours=1), title="renamed there")
    await index_import.mark_index_stale()
    assert await index_import.import_agent_server_threads() == 5

    kept, caught_up = await index.get("t2"), await index.get("t3")
    assert kept is not None and kept.metadata["title"] == "renamed here"
    assert caught_up is not None and caught_up.metadata == {"title": "renamed there"}
    assert memory_event_claims.held == {("threads", "index-current")}


async def test_an_import_killed_partway_runs_again_on_the_next_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(index_import, "_PAGE", 2)
    threads = _Threads([_thread(n, updated=_START) for n in range(5)])
    threads.killed_at = 2
    monkeypatch.setattr(index_import, "RUNTIMES", _Runtimes(threads))

    with pytest.raises(asyncio.CancelledError):
        await index_import.import_agent_server_threads()
    threads.killed_at = None

    assert await index_import.import_agent_server_threads() == 5
    assert await index.get("t4") is not None
