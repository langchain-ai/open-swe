"""Tests for agent.utils.shared_cache."""

import asyncio
import hashlib
import logging
import threading
from collections.abc import Awaitable, Callable, Sequence
from typing import NoReturn

import pytest
from pydantic import TypeAdapter

from agent.utils import shared_cache, ttl_cache
from tests.conftest import FakeStore
from tests.support.eventually import eventually

_NAMESPACE = ["shared-cache-tests", "catalog"]
_KEY = "catalog-key"
_ITEM_KEY = hashlib.sha256(_KEY.encode()).hexdigest()


async def _cached(loader: Callable[[], Awaitable[dict[str, int]]]) -> dict[str, int]:
    return await shared_cache.cached(
        _NAMESPACE, _KEY, 60.0, loader, dump=lambda v: v, load=lambda v: v
    )


def _stored(fake_store: FakeStore) -> object:
    return fake_store.values(_NAMESPACE)[_ITEM_KEY]["value"]


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == shared_cache.__name__ and record.levelno == logging.WARNING
    ]


def _swallowed(record: logging.LogRecord) -> BaseException | None:
    """The exception a warning says it swallowed, if it attached one."""
    return record.exc_info[1] if record.exc_info else None


def _seed_stale_item(fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """An item written at t=1000 and read at t=1060: past the 60s ttl, so stale."""
    clock = {"now": 1_000.0}
    monkeypatch.setattr(shared_cache, "_now", lambda: clock["now"])
    fake_store.seed(_NAMESPACE, _ITEM_KEY, {"stored_at": 1_000.0, "value": {"n": 1}})
    clock["now"] = 1_060.0


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

    await eventually(lambda: _stored(fake_store) == {"n": 2})

    assert calls == 1
    assert fake_store.values(_NAMESPACE)[_ITEM_KEY] == {
        "stored_at": 1_060.0,
        "value": {"n": 2},
    }


async def test_store_read_failure_falls_back_to_loader(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A Store outage on read must not fail the call; it degrades to the loader."""
    outage = RuntimeError("store unavailable")

    async def failing_get_item(_namespace: Sequence[str], _key: str) -> dict[str, object]:
        raise outage

    monkeypatch.setattr(fake_store, "get_item", failing_get_item)

    async def loader() -> dict[str, int]:
        return {"n": 5}

    result = await shared_cache.cached(
        _NAMESPACE, _KEY, 60.0, loader, dump=lambda v: v, load=lambda v: v
    )

    assert result == {"n": 5}
    assert [_swallowed(record) for record in _warnings(caplog)] == [outage]


async def test_store_write_failure_does_not_fail_the_call(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A Store outage on write must not fail a call that already has a value to return."""
    outage = RuntimeError("store unavailable")

    async def failing_put_item(
        _namespace: Sequence[str], _key: str, _value: dict[str, object]
    ) -> None:
        raise outage

    monkeypatch.setattr(fake_store, "put_item", failing_put_item)

    async def loader() -> dict[str, int]:
        return {"n": 3}

    result = await shared_cache.cached(
        _NAMESPACE, _KEY, 60.0, loader, dump=lambda v: v, load=lambda v: v
    )

    assert result == {"n": 3}
    assert [_swallowed(record) for record in _warnings(caplog)] == [outage]


async def test_clear_cancels_a_pending_refresh(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reset between tests must not let one test's refresh write into the next."""
    _seed_stale_item(fake_store, monkeypatch)
    started = asyncio.Event()
    release = asyncio.Event()
    cancelled = asyncio.Event()

    async def held_loader() -> dict[str, int]:
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return {"n": 2}

    assert await _cached(held_loader) == {"n": 1}
    await asyncio.wait_for(started.wait(), timeout=1)

    shared_cache.clear()
    release.set()
    await eventually(lambda: cancelled.is_set() or _stored(fake_store) != {"n": 1})

    assert cancelled.is_set()
    assert _stored(fake_store) == {"n": 1}


async def test_refresh_held_on_another_event_loop_does_not_block_this_one(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_stale_item(fake_store, monkeypatch)
    other_loop = asyncio.new_event_loop()
    other_thread = threading.Thread(target=other_loop.run_forever, daemon=True)
    other_thread.start()
    release = asyncio.Event()

    async def held_loader() -> dict[str, int]:
        await release.wait()
        return {"n": 0}

    async def fresh_loader() -> dict[str, int]:
        return {"n": 2}

    async def settle_other_loop() -> None:
        release.set()
        pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        await asyncio.gather(*pending)

    try:
        stale = asyncio.run_coroutine_threadsafe(_cached(held_loader), other_loop)
        assert await asyncio.wrap_future(stale) == {"n": 1}
        ttl_cache.clear()  # this loop's copy is gone; the other loop's refresh is still held

        assert await _cached(fresh_loader) == {"n": 1}
        await eventually(lambda: _stored(fake_store) == {"n": 2})
    finally:
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(settle_other_loop(), other_loop))
        other_loop.call_soon_threadsafe(other_loop.stop)
        other_thread.join(timeout=5)
        other_loop.close()


async def test_first_call_after_a_completed_refresh_returns_the_new_value(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_stale_item(fake_store, monkeypatch)

    async def loader() -> dict[str, int]:
        return {"n": 2}

    assert await _cached(loader) == {"n": 1}
    await eventually(lambda: _stored(fake_store) == {"n": 2})

    async def unexpected_loader() -> dict[str, int]:
        raise AssertionError("the refreshed value must already be on this worker")

    assert await _cached(unexpected_loader) == {"n": 2}


async def test_item_older_than_max_stale_makes_the_caller_wait_for_the_loader(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = {"now": 1_000.0}
    monkeypatch.setattr(shared_cache, "_now", lambda: clock["now"])
    fake_store.seed(_NAMESPACE, _ITEM_KEY, {"stored_at": 1_000.0, "value": {"n": 1}})
    clock["now"] = 1_000.0 + 3_601.0  # past both the 60s ttl and the hour-long stale bound

    async def loader() -> dict[str, int]:
        return {"n": 2}

    result = await shared_cache.cached(
        _NAMESPACE,
        _KEY,
        60.0,
        loader,
        dump=lambda v: v,
        load=lambda v: v,
        max_stale_seconds=3_600.0,
    )

    assert result == {"n": 2}
    assert _stored(fake_store) == {"n": 2}


async def test_failed_refresh_is_logged_and_the_stale_value_kept(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _seed_stale_item(fake_store, monkeypatch)
    failure = RuntimeError("upstream unavailable")

    async def failing_loader() -> dict[str, int]:
        raise failure

    assert await _cached(failing_loader) == {"n": 1}
    await eventually(lambda: bool(_warnings(caplog)))

    assert [_swallowed(record) for record in _warnings(caplog)] == [failure]
    assert _stored(fake_store) == {"n": 1}


async def test_undecodable_item_falls_back_to_the_loader_without_logging_its_contents(
    fake_store: FakeStore, caplog: pytest.LogCaptureFixture
) -> None:
    """A pydantic ValidationError echoes its input, which can be a stored identity's email."""
    fake_store.seed(
        _NAMESPACE, _ITEM_KEY, {"stored_at": 1_000.0, "value": {"n": "mason@example.com"}}
    )

    async def loader() -> dict[str, int]:
        return {"n": 4}

    result = await shared_cache.cached(
        _NAMESPACE,
        _KEY,
        60.0,
        loader,
        dump=lambda v: v,
        load=TypeAdapter(dict[str, int]).validate_python,
    )

    assert result == {"n": 4}
    assert _stored(fake_store) == {"n": 4}
    [record] = _warnings(caplog)
    assert getattr(record, "error_type", None) == "ValidationError"
    assert _swallowed(record) is None
    assert "mason@example.com" not in caplog.text


async def test_stale_reads_share_one_background_refresh(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_stale_item(fake_store, monkeypatch)
    release = asyncio.Event()
    calls = 0

    async def held_loader() -> dict[str, int]:
        nonlocal calls
        calls += 1
        await release.wait()
        return {"n": 2}

    assert await _cached(held_loader) == {"n": 1}
    ttl_cache.clear()  # this worker's copy expires while that refresh is still running
    assert await _cached(held_loader) == {"n": 1}
    # Both reads finished before either refresh could start, so this sees every one.
    await eventually(lambda: calls > 0)
    assert calls == 1

    release.set()
    await eventually(lambda: _stored(fake_store) == {"n": 2})
    assert calls == 1


@pytest.mark.parametrize("operation", ["get_item", "put_item"])
async def test_slow_store_falls_back_like_a_failing_one(
    fake_store: FakeStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    operation: str,
) -> None:
    async def hang(*_args: object) -> NoReturn:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(fake_store, operation, hang)
    monkeypatch.setattr(shared_cache, "_STORE_TIMEOUT_SECONDS", 0.01)

    async def loader() -> dict[str, int]:
        return {"n": 6}

    async with asyncio.timeout(1):  # a call a stalled Store can hold up hangs here
        assert await _cached(loader) == {"n": 6}

    assert [type(_swallowed(record)) for record in _warnings(caplog)] == [TimeoutError]
