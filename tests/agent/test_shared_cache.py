import asyncio
import sys
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, TypeAdapter

from agent.utils import shared_cache


class Metadata(BaseModel):
    name: str
    created_at: datetime


async def test_serialized_results_survive_worker_local_state_and_preserve_types(monkeypatch):
    adapter = TypeAdapter(list[Metadata] | None)
    value = [Metadata(name="tool", created_at=datetime(2026, 1, 1, tzinfo=UTC))]
    loader = AsyncMock(return_value=value)
    first = await shared_cache.cached("catalog", 60, loader, adapter=adapter)
    first[0].name = "changed locally"
    monkeypatch.setattr(shared_cache, "_REFRESHES", {})
    second = await shared_cache.cached("catalog", 60, loader, adapter=adapter)
    assert second[0].name == "tool"
    assert second[0].created_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert loader.await_count == 1
    negative = AsyncMock(return_value=None)
    assert await shared_cache.cached("absent", 60, negative, adapter=adapter) is None
    assert await shared_cache.cached("absent", 60, negative, adapter=adapter) is None
    assert negative.await_count == 1


async def test_stale_returns_immediately_but_hard_expiry_waits_for_loader(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(shared_cache.time, "time", lambda: clock[0])
    adapter = TypeAdapter(str)
    await shared_cache.set_cached("key", "old", 10, adapter=adapter, max_age=30)
    started, release = asyncio.Event(), asyncio.Event()

    async def load():
        started.set()
        await release.wait()
        return "new"

    clock[0] = 111
    assert await shared_cache.cached("key", 10, load, adapter=adapter, max_age=30) == "old"
    await started.wait()
    clock[0] = 131
    waiting = asyncio.create_task(shared_cache.cached("key", 10, load, adapter=adapter, max_age=30))
    await asyncio.sleep(0)
    assert not waiting.done()
    release.set()
    assert await waiting == "new"


async def test_loader_failure_never_extends_hard_expiry(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(shared_cache.time, "time", lambda: clock[0])
    adapter = TypeAdapter(str)
    await shared_cache.set_cached("key", "old", 10, adapter=adapter, max_age=30)
    loader = AsyncMock(side_effect=RuntimeError("upstream unavailable"))
    clock[0] = 111
    assert await shared_cache.cached("key", 10, loader, adapter=adapter, max_age=30) == "old"
    await asyncio.gather(*shared_cache._REFRESHES.values(), return_exceptions=True)
    clock[0] = 131
    with pytest.raises(RuntimeError, match="upstream unavailable"):
        await shared_cache.cached("key", 10, loader, adapter=adapter, max_age=30)


async def test_cache_outage_fails_open_without_retrying_source_errors(monkeypatch, caplog):
    monkeypatch.setattr(shared_cache, "_get", AsyncMock(side_effect=RuntimeError("cache down")))
    monkeypatch.setattr(shared_cache, "_set", AsyncMock(side_effect=RuntimeError("cache down")))
    loader = AsyncMock(return_value="live")
    assert await shared_cache.cached("key", 10, loader, adapter=TypeAdapter(str)) == "live"
    assert loader.await_count == 1
    assert "Shared cache read unavailable" in caplog.text
    assert "Shared cache write unavailable" in caplog.text
    loader.side_effect = ValueError("source failed")
    with pytest.raises(ValueError, match="source failed"):
        await shared_cache.cached("key", 10, loader, adapter=TypeAdapter(str))
    assert loader.await_count == 2


@pytest.mark.parametrize("operation", ["read", "write"])
async def test_stalled_cache_fails_open(monkeypatch, caplog, operation):
    cancelled = asyncio.Event()

    async def stalled(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(shared_cache, "_CACHE_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(
        sys.modules["langgraph_sdk.cache"],
        "cache_get" if operation == "read" else "cache_set",
        stalled,
    )
    loader = AsyncMock(return_value="live")
    async with asyncio.timeout(1):
        assert await shared_cache.cached("key", 10, loader, adapter=TypeAdapter(str)) == "live"
    assert cancelled.is_set()
    assert loader.await_count == 1
    assert f"Shared cache {operation} unavailable" in caplog.text
    assert any(getattr(record, "cache_error", None) == "TimeoutError" for record in caplog.records)


async def test_concurrent_misses_share_only_the_inflight_loader():
    loader = AsyncMock(return_value="value")
    assert (
        await asyncio.gather(
            *(shared_cache.cached("key", 10, loader, adapter=TypeAdapter(str)) for _ in range(10))
        )
        == ["value"] * 10
    )
    assert loader.await_count == 1


async def test_schema_mismatch_is_a_miss(shared_cache_backend):
    await shared_cache.set_cached("key", "wrong shape", 60, adapter=TypeAdapter(str))
    assert await shared_cache.get_cached("key", adapter=TypeAdapter(Metadata)) is None
    loader = AsyncMock(return_value=Metadata(name="new", created_at=datetime.now(UTC)))
    assert (
        await shared_cache.cached("key", 60, loader, adapter=TypeAdapter(Metadata))
    ).name == "new"
