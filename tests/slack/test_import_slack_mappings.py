from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from openswe.database import store_imports
from openswe.database.store_imports import StoreImport
from openswe.slack import import_mappings
from openswe.slack.import_mappings import import_store_slack_mappings
from openswe.slack.mappings import (
    bind_slack_thread_id,
    lookup_slack_run_mapping,
    lookup_slack_run_message_mapping,
    lookup_slack_thread_id,
    lookup_slack_thread_run_mapping,
    resolve_slack_thread_id,
    store_slack_run_mapping,
)
from openswe.store import StoreEntry
from openswe.thread_ids import slack_thread_id

_SAVED = datetime(2026, 1, 1, tzinfo=UTC)


class _Store:
    """The Store as the import reads it: searched by namespace prefix, with save times."""

    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], tuple[dict[str, str], datetime]] = {}

    def seed(self, namespace: str, key: str, value: dict[str, str], seconds: int = 0) -> None:
        self.items[((namespace, "C1"), key)] = (value, _SAVED + timedelta(seconds=seconds))

    def save_now(self, namespace: str, key: str, value: dict[str, str]) -> None:
        self.items[((namespace, "C1"), key)] = (value, datetime.now(UTC))

    async def search(self, namespace: Sequence[str]) -> list[StoreEntry]:
        return [
            StoreEntry(list(item_namespace), dict(value), key, saved_at)
            for (item_namespace, key), (value, saved_at) in self.items.items()
            if item_namespace[: len(namespace)] == tuple(namespace)
        ]

    async def delete(self, namespace: Sequence[str], key: str) -> None:
        self.items.pop((tuple(namespace), key), None)


class _Threads:
    async def search(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []


class _Client:
    threads = _Threads()


@pytest.fixture
def store(registry_db: None, monkeypatch: pytest.MonkeyPatch) -> _Store:
    fake = _Store()
    monkeypatch.setattr(import_mappings, "search_all_entries", fake.search)
    monkeypatch.setattr(import_mappings, "delete_value", fake.delete)
    return fake


def _retire_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_imports, "RETIRE_STORE_COPY_AFTER", timedelta())
    monkeypatch.setattr(import_mappings, "RETIRE_STORE_COPY_AFTER", timedelta())


async def test_store_import_copies_every_mapping_then_retires_the_store_records(
    store: _Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = {"thread_ts": "1.0", "triggering_user_id": "U1", "agent_thread_id": "thread-1"}
    store.seed("slack_thread_map", "1.0", {"thread_id": "thread-1"})
    store.seed("slack_thread_map", "2.0", {"nonce": "detached"})
    store.seed("slack_thread_map", "3.0", {"thread_id": "older-thread"})
    await bind_slack_thread_id("C1", "3.0", "thread-3")
    store.seed("slack_run_map", "run:run-1", {**run, "run_id": "run-1", "message_ts": "1.1"}, 1)
    store.seed("slack_run_map", "message:1.1", {**run, "run_id": "run-1", "message_ts": "1.1"}, 1)
    store.seed(
        "slack_run_map", "thread:1.0", {**run, "run_id": "run-2", "trace_message_ts": "1.05"}, 2
    )
    store.seed("slack_run_map", "run:run-2", {**run, "run_id": "run-2"}, 2)

    assert await import_store_slack_mappings() == StoreImport(moved=5, waiting=1)
    assert await lookup_slack_thread_id("C1", "1.0") == "thread-1"
    assert await lookup_slack_thread_id("C1", "3.0") == "thread-3"
    detached: Any = _Client()
    assert await resolve_slack_thread_id(detached, "C1", "2.0") == slack_thread_id(
        "C1", "2.0", "detached"
    )
    current = await lookup_slack_thread_run_mapping("C1", "1.0")
    assert current is not None
    assert (current.run_id, current.trace_message_ts) == ("run-2", "1.05")
    first = await lookup_slack_run_message_mapping("C1", "run-1")
    assert first is not None and first.message_ts == "1.1"
    message = await lookup_slack_run_mapping("C1", "1.1")
    assert message is not None
    assert (message.run_id, message.triggering_user_id, message.agent_thread_id) == (
        "run-1",
        "U1",
        "thread-1",
    )
    assert await import_store_slack_mappings() == StoreImport(waiting=6)

    store.save_now("slack_run_map", "thread:1.0", {**run, "run_id": "run-3"})
    store.save_now("slack_run_map", "run:run-3", {**run, "run_id": "run-3"})
    assert await import_store_slack_mappings() == StoreImport(moved=1, waiting=6)
    current = await lookup_slack_thread_run_mapping("C1", "1.0")
    assert current is not None and current.run_id == "run-3"

    _retire_immediately(monkeypatch)
    assert await import_store_slack_mappings() == StoreImport(moved=8)
    assert store.items == {}
    assert await lookup_slack_thread_id("C1", "1.0") == "thread-1"


async def test_store_import_drops_copies_an_old_replica_deleted_from_the_store(
    store: _Store,
) -> None:
    run = {"thread_ts": "1.0", "triggering_user_id": "U1"}
    store.seed("slack_run_map", "thread:1.0", {**run, "run_id": "run-1"})
    store.seed("slack_run_map", "run:run-1", {**run, "run_id": "run-1", "message_ts": "1.1"})
    store.seed("slack_run_map", "message:1.1", {**run, "run_id": "run-1"})
    store.seed("slack_run_map", "message:1.2", {**run, "run_id": "run-2"})
    assert await import_store_slack_mappings() == StoreImport(moved=3)
    await store_slack_run_mapping("C1", "1.0", "run-2", message_ts="1.2")

    store.items.clear()

    assert await import_store_slack_mappings() == StoreImport(moved=2)
    assert await lookup_slack_run_message_mapping("C1", "run-1") is None
    assert await lookup_slack_run_mapping("C1", "1.1") is None
    current = await lookup_slack_thread_run_mapping("C1", "1.0")
    assert current is not None and current.run_id == "run-2"
    message = await lookup_slack_run_mapping("C1", "1.2")
    assert message is not None and message.run_id == "run-2"
