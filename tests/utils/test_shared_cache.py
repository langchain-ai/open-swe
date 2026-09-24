"""Tests for agent.utils.shared_cache."""

import asyncio
import hashlib
from collections.abc import Sequence

import pytest

from agent.utils import shared_cache, ttl_cache
from tests.conftest import FakeStore

_NAMESPACE = ["shared-cache-tests", "catalog"]
_KEY = "catalog-key"
_ITEM_KEY = hashlib.sha256(_KEY.encode()).hexdigest()


async def test_second_worker_reads_store_value_without_calling_loader(
    fake_store: FakeStore,
) -> None:
    """A value one worker loads is visible to a worker with an empty in-process cache."""
    calls = 0

    async def loader() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"n": 1}

    first = await shared_cache.cached(
        _NAMESPACE, _KEY, 60.0, loader, dump=lambda v: v, load=lambda v: v
    )
    assert first == {"n": 1}
    assert calls == 1

    ttl_cache.clear()  # a second worker starts with its own, empty in-process cache

    async def unexpected_loader() -> dict[str, int]:
        raise AssertionError("loader must not run when the Store already has a fresh value")

    second = await shared_cache.cached(
        _NAMESPACE, _KEY, 60.0, unexpected_loader, dump=lambda v: v, load=lambda v: v
    )
    assert second == {"n": 1}


async def test_stale_store_value_served_while_background_refresh_stores_new_one(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale item is returned immediately; a single background refresh replaces it."""
    clock = {"now": 1_000.0}
    monkeypatch.setattr(shared_cache, "_now", lambda: clock["now"])
    fake_store.seed(_NAMESPACE, _ITEM_KEY, {"stored_at": 1_000.0, "value": {"n": 1}})
    clock["now"] = 1_060.0  # 60s later: at the 60s ttl boundary, so stale

    calls = 0

    async def loader() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"n": 2}

    result = await shared_cache.cached(
        _NAMESPACE, _KEY, 60.0, loader, dump=lambda v: v, load=lambda v: v
    )

    assert result == {"n": 1}
    assert calls == 0  # the refresh runs in the background, not on this call

    await asyncio.gather(*shared_cache._REFRESH_TASKS.values())

    assert calls == 1
    assert fake_store.values(_NAMESPACE)[_ITEM_KEY] == {
        "stored_at": 1_060.0,
        "value": {"n": 2},
    }


async def test_store_read_failure_falls_back_to_loader(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Store outage on read must not fail the call; it degrades to the loader."""

    async def failing_get_item(_namespace: Sequence[str], _key: str) -> dict[str, object]:
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(fake_store, "get_item", failing_get_item)

    async def loader() -> dict[str, int]:
        return {"n": 5}

    result = await shared_cache.cached(
        _NAMESPACE, _KEY, 60.0, loader, dump=lambda v: v, load=lambda v: v
    )

    assert result == {"n": 5}


async def test_store_write_failure_does_not_fail_the_call(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Store outage on write must not fail a call that already has a value to return."""

    async def failing_put_item(
        _namespace: Sequence[str], _key: str, _value: dict[str, object]
    ) -> None:
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(fake_store, "put_item", failing_put_item)

    async def loader() -> dict[str, int]:
        return {"n": 3}

    result = await shared_cache.cached(
        _NAMESPACE, _KEY, 60.0, loader, dump=lambda v: v, load=lambda v: v
    )

    assert result == {"n": 3}
