import asyncio
from typing import Any

import pytest

from openswe.utils import thread_reads
from openswe.utils.thread_reads import read_thread, shared_thread_reads


class _Threads:
    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.reads = 0

    async def get(self, thread_id: str) -> Any:
        self.reads += 1
        await asyncio.sleep(0)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _Client:
    def __init__(self, threads: _Threads) -> None:
        self.threads = threads


@pytest.fixture
def threads(monkeypatch: pytest.MonkeyPatch) -> _Threads:
    fake = _Threads()
    monkeypatch.setattr(thread_reads.langgraph_sdk, "get_client", lambda: _Client(fake))
    return fake


async def test_lookups_in_a_scope_share_one_read(threads: _Threads) -> None:
    threads.outcomes = [{"thread_id": "t", "n": 1}, {"thread_id": "t", "n": 2}]

    async with shared_thread_reads():
        first, second = await asyncio.gather(read_thread("t"), read_thread("t"))
        third = await read_thread("t")
    outside = await read_thread("t")

    assert first == second == third == {"thread_id": "t", "n": 1}
    assert outside == {"thread_id": "t", "n": 2}
    assert threads.reads == 2


async def test_a_failed_read_is_not_shared(threads: _Threads) -> None:
    threads.outcomes = [RuntimeError("api down"), {"thread_id": "t"}]

    async with shared_thread_reads():
        with pytest.raises(RuntimeError):
            await read_thread("t")
        assert await read_thread("t") == {"thread_id": "t"}

    assert threads.reads == 2


async def test_a_nested_scope_shares_the_outer_reads(threads: _Threads) -> None:
    threads.outcomes = [{"thread_id": "t", "n": 1}]

    async with shared_thread_reads():
        planned = await read_thread("t")
        async with shared_thread_reads():
            prepared = await read_thread("t")

    assert planned == prepared
    assert threads.reads == 1
