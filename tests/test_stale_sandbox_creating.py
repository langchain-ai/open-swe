"""Tests for the stale SANDBOX_CREATING sentinel escape path (#1116)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

import agent.server as server


class _FakeThreads:
    def __init__(self, thread: dict[str, Any]) -> None:
        self._thread = thread
        self.calls: list[str] = []

    async def get(self, thread_id: str) -> dict[str, Any]:
        self.calls.append(thread_id)
        return self._thread


class _FakeClient:
    def __init__(self, thread: dict[str, Any]) -> None:
        self.threads = _FakeThreads(thread)


class _FlakyThreads:
    def __init__(self, failures: int, final_thread: dict[str, Any]) -> None:
        self._failures = failures
        self._final_thread = final_thread
        self.attempts = 0

    async def get(self, thread_id: str) -> dict[str, Any]:
        del thread_id
        self.attempts += 1
        if self.attempts <= self._failures:
            raise ConnectionError("transient")
        return self._final_thread


class _FlakyClient:
    def __init__(self, flaky_threads: _FlakyThreads) -> None:
        self.threads = flaky_threads


_UNSET: Any = object()


def _fake_thread(
    sandbox_id: str | None,
    *,
    age_seconds: float = 0.0,
    updated_at: Any = _UNSET,
) -> dict[str, Any]:
    metadata = {"sandbox_id": sandbox_id} if sandbox_id is not None else {}
    if updated_at is _UNSET:
        updated_at = (datetime.now(UTC) - timedelta(seconds=age_seconds)).isoformat()
    thread: dict[str, Any] = {"thread_id": "test-thread", "metadata": metadata}
    if updated_at is not None:
        thread["updated_at"] = updated_at
    return thread


async def test_stale_sentinel_returns_none_without_waiting(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Stale sentinel + no cached backend returns None on first poll (#1116 fix)."""
    stale_age = server.SANDBOX_CREATING_STALE_AFTER_SECONDS + 30
    fake_client = _FakeClient(_fake_thread("__creating__", age_seconds=stale_age))

    monkeypatch.setattr(server, "client", fake_client)
    monkeypatch.setattr(server, "SANDBOX_BACKENDS", {})

    with caplog.at_level(logging.WARNING, logger="agent.server"):
        result = await server._wait_for_sandbox_id("test-thread")

    assert result is None
    assert fake_client.threads.calls == ["test-thread"]
    assert any("stale" in rec.message.lower() for rec in caplog.records)


async def test_real_sandbox_id_returns_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real sandbox_id in thread metadata is returned on the first poll."""
    fake_client = _FakeClient(_fake_thread("sandbox-abc-123"))

    monkeypatch.setattr(server, "client", fake_client)
    monkeypatch.setattr(server, "SANDBOX_BACKENDS", {})

    result = await server._wait_for_sandbox_id("test-thread")

    assert result == "sandbox-abc-123"
    assert fake_client.threads.calls == ["test-thread"]


async def test_fresh_sentinel_does_not_bail_with_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fresh sentinel stays in the wait loop (preserves concurrent-worker case)."""
    fake_client = _FakeClient(_fake_thread("__creating__", age_seconds=0))

    monkeypatch.setattr(server, "client", fake_client)
    monkeypatch.setattr(server, "SANDBOX_BACKENDS", {})
    monkeypatch.setattr(server, "SANDBOX_CREATION_TIMEOUT", 0.5)
    monkeypatch.setattr(server, "SANDBOX_POLL_INTERVAL", 0.1)

    with pytest.raises(TimeoutError, match="Timeout waiting for sandbox creation"):
        await server._wait_for_sandbox_id("test-thread")


async def test_cached_backend_skips_stale_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cached backend in this process blocks the stale escape even on an
    ancient sentinel so we don't clobber a peer worker's state."""
    stale_age = server.SANDBOX_CREATING_STALE_AFTER_SECONDS + 30
    fake_client = _FakeClient(_fake_thread("__creating__", age_seconds=stale_age))
    cached_backend = SimpleNamespace(id="cached-sandbox")

    monkeypatch.setattr(server, "client", fake_client)
    monkeypatch.setattr(server, "SANDBOX_BACKENDS", {"test-thread": cached_backend})
    monkeypatch.setattr(server, "SANDBOX_CREATION_TIMEOUT", 0.3)
    monkeypatch.setattr(server, "SANDBOX_POLL_INTERVAL", 0.1)

    with pytest.raises(TimeoutError):
        await server._wait_for_sandbox_id("test-thread")


async def test_missing_updated_at_preserves_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing `updated_at` does not trigger the stale escape."""
    fake_client = _FakeClient(_fake_thread("__creating__", updated_at=None))

    monkeypatch.setattr(server, "client", fake_client)
    monkeypatch.setattr(server, "SANDBOX_BACKENDS", {})
    monkeypatch.setattr(server, "SANDBOX_CREATION_TIMEOUT", 0.3)
    monkeypatch.setattr(server, "SANDBOX_POLL_INTERVAL", 0.1)

    with pytest.raises(TimeoutError):
        await server._wait_for_sandbox_id("test-thread")


async def test_empty_updated_at_preserves_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty-string `updated_at` does not trigger the stale escape."""
    fake_client = _FakeClient(_fake_thread("__creating__", updated_at=""))

    monkeypatch.setattr(server, "client", fake_client)
    monkeypatch.setattr(server, "SANDBOX_BACKENDS", {})
    monkeypatch.setattr(server, "SANDBOX_CREATION_TIMEOUT", 0.3)
    monkeypatch.setattr(server, "SANDBOX_POLL_INTERVAL", 0.1)

    with pytest.raises(TimeoutError):
        await server._wait_for_sandbox_id("test-thread")


async def test_unparseable_updated_at_preserves_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unparseable `updated_at` does not trigger the stale escape."""
    fake_client = _FakeClient(_fake_thread("__creating__", updated_at="not a date"))

    monkeypatch.setattr(server, "client", fake_client)
    monkeypatch.setattr(server, "SANDBOX_BACKENDS", {})
    monkeypatch.setattr(server, "SANDBOX_CREATION_TIMEOUT", 0.3)
    monkeypatch.setattr(server, "SANDBOX_POLL_INTERVAL", 0.1)

    with pytest.raises(TimeoutError):
        await server._wait_for_sandbox_id("test-thread")


async def test_tz_naive_updated_at_is_assumed_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tz-naive `updated_at` is interpreted as UTC rather than failing."""
    stale_age = server.SANDBOX_CREATING_STALE_AFTER_SECONDS + 30
    naive_iso = (datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=stale_age)).isoformat()
    fake_client = _FakeClient(_fake_thread("__creating__", updated_at=naive_iso))

    monkeypatch.setattr(server, "client", fake_client)
    monkeypatch.setattr(server, "SANDBOX_BACKENDS", {})

    result = await server._wait_for_sandbox_id("test-thread")

    assert result is None


async def test_single_transient_read_error_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One transient read failure falls through to the next poll."""
    flaky = _FlakyThreads(failures=1, final_thread=_fake_thread("sandbox-recovered"))

    monkeypatch.setattr(server, "client", _FlakyClient(flaky))
    monkeypatch.setattr(server, "SANDBOX_BACKENDS", {})
    monkeypatch.setattr(server, "SANDBOX_POLL_INTERVAL", 0.05)

    result = await server._wait_for_sandbox_id("test-thread")

    assert result == "sandbox-recovered"
    assert flaky.attempts == 2


async def test_multiple_transient_read_errors_are_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Several consecutive transient failures do not abort the wait."""
    flaky = _FlakyThreads(failures=3, final_thread=_fake_thread("sandbox-eventually"))

    monkeypatch.setattr(server, "client", _FlakyClient(flaky))
    monkeypatch.setattr(server, "SANDBOX_BACKENDS", {})
    monkeypatch.setattr(server, "SANDBOX_POLL_INTERVAL", 0.05)

    result = await server._wait_for_sandbox_id("test-thread")

    assert result == "sandbox-eventually"
    assert flaky.attempts == 4


async def test_persistent_read_error_hits_creation_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read that never recovers eventually hits SANDBOX_CREATION_TIMEOUT."""
    flaky = _FlakyThreads(failures=10_000, final_thread=_fake_thread("never"))

    monkeypatch.setattr(server, "client", _FlakyClient(flaky))
    monkeypatch.setattr(server, "SANDBOX_BACKENDS", {})
    monkeypatch.setattr(server, "SANDBOX_CREATION_TIMEOUT", 0.3)
    monkeypatch.setattr(server, "SANDBOX_POLL_INTERVAL", 0.1)

    with pytest.raises(TimeoutError):
        await server._wait_for_sandbox_id("test-thread")
