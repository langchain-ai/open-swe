import asyncio
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import httpx2
import pytest
from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.errors import (
    APIConnectionError,
    ConflictError,
    InternalServerError,
    NotFoundError,
)

from openswe.database import postgres
from openswe.slack import client as slack_client
from openswe.slack.client import (
    SlackThreadMappingError,
    bind_slack_thread_id,
    delete_slack_thread_associations,
    lookup_slack_run_message_mapping,
    lookup_slack_thread_id,
    resolve_slack_thread_id,
    slack_thread_mutation_lock,
    store_slack_message_run_mapping,
    store_slack_run_mapping,
)


class _Store:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: tuple[str, ...], key: str) -> dict[str, Any] | None:
        return self.items.get((tuple(namespace), key))

    async def put_item(self, namespace: tuple[str, ...], key: str, value: dict[str, Any]) -> None:
        self.items[(tuple(namespace), key)] = {
            "namespace": list(namespace),
            "key": key,
            "value": value,
        }

    async def delete_item(self, namespace: tuple[str, ...], *, key: str) -> None:
        self.items.pop((tuple(namespace), key), None)

    async def search_items(
        self,
        namespace: tuple[str, ...],
        *,
        filter: dict[str, Any] | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> dict[str, Any]:
        matches = [
            item
            for (item_namespace, _), item in self.items.items()
            if item_namespace == tuple(namespace)
            and all(item["value"].get(key) == value for key, value in (filter or {}).items())
        ]
        return {"items": matches[offset : offset + limit]}


class _Threads:
    def __init__(self, matches: list[dict[str, Any]] | None = None) -> None:
        self.matches = matches or []
        self.lock_ids: set[str] = set()
        self.locks: dict[str, dict[str, object]] = {}

    async def create(self, *, thread_id: str, **kwargs: object) -> None:
        if thread_id in self.lock_ids:
            response = httpx2.Response(409, request=httpx2.Request("POST", "http://test/threads"))
            raise ConflictError("already exists", response=response, body=None)
        self.lock_ids.add(thread_id)
        self.locks[thread_id] = {
            "thread_id": thread_id,
            "created_at": datetime.now(UTC).isoformat(),
            "metadata": kwargs.get("metadata") or {},
        }

    async def delete(self, thread_id: str) -> None:
        self.lock_ids.discard(thread_id)
        self.locks.pop(thread_id, None)

    async def search(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.matches

    async def get(self, thread_id: str) -> dict[str, Any]:
        if thread_id in self.locks:
            thread = self.locks[thread_id]
            await asyncio.sleep(0)
            return thread
        match = next((match for match in self.matches if match.get("thread_id") == thread_id), None)
        if match is not None:
            return match
        response = httpx2.Response(404, request=httpx2.Request("GET", "http://test/threads"))
        raise NotFoundError("not found", response=response, body=None)


class _Client:
    def __init__(self, matches: list[dict[str, Any]] | None = None) -> None:
        self.store = _Store()
        self.threads = _Threads(matches)


class _MutationConnection:
    def __init__(self, locks: dict[str, asyncio.Lock]) -> None:
        self.locks = locks
        self.held: asyncio.Lock | None = None

    async def execute(self, _statement: object, parameters: Mapping[str, str]) -> None:
        lock = self.locks.setdefault(parameters["subject"], asyncio.Lock())
        await lock.acquire()
        self.held = lock


def patch_mutation_transactions(monkeypatch: pytest.MonkeyPatch) -> None:
    locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def transaction() -> AsyncIterator[_MutationConnection]:
        conn = _MutationConnection(locks)
        try:
            yield conn
        finally:
            if conn.held is not None:
                conn.held.release()

    monkeypatch.setattr(postgres, "transaction", transaction)


@pytest.fixture(autouse=True)
def _mutation_transactions(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_mutation_transactions(monkeypatch)


def _legacy_thread(
    thread_id: str, channel_id: str = "C1", thread_ts: str = "1.0"
) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "metadata": {
            "source_context": {"slack_thread": {"channel_id": channel_id, "thread_ts": thread_ts}}
        },
    }


@pytest.mark.asyncio
async def test_thread_mutation_lock_returns_active_thread() -> None:
    client: Any = _Client(matches=[_legacy_thread("agent-thread")])

    async with slack_thread_mutation_lock(client, "C1", "1.0", thread_id="agent-thread") as active:
        assert active == {"channel_id": "C1", "thread_ts": "1.0"}


@pytest.mark.asyncio
async def test_thread_mutation_lock_serializes_same_thread() -> None:
    client: Any = _Client()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def first() -> None:
        async with slack_thread_mutation_lock(client, "C1", "1.0"):
            held = next(iter(client.threads.locks.values()))
            held["metadata"]["lock_expires_at"] = (
                datetime.now(UTC) - timedelta(minutes=2)
            ).isoformat()
            first_entered.set()
            await release_first.wait()

    async def second() -> None:
        await first_entered.wait()
        async with slack_thread_mutation_lock(client, "C1", "1.0"):
            second_entered.set()

    first_task = asyncio.create_task(first())
    second_task = asyncio.create_task(second())
    await first_entered.wait()
    await asyncio.sleep(0.01)
    assert second_entered.is_set() is False
    release_first.set()
    await asyncio.gather(first_task, second_task)
    assert second_entered.is_set() is True


@pytest.mark.parametrize("legacy", [False, True])
async def test_expired_orphan_recovery_serializes_later_replies(legacy: bool) -> None:
    client = _Client()
    lock_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "open-swe:slack-thread-lock:C1:1.0"))
    old = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
    await client.threads.create(thread_id=lock_id)
    client.threads.locks[lock_id] = {
        "created_at": old,
        "metadata": {} if legacy else {"lock_expires_at": old, "lock_owner": "orphan"},
    }
    active = 0
    replies = 0

    async def reply() -> None:
        nonlocal active, replies
        worker = _Client()
        worker.threads = client.threads
        async with slack_thread_mutation_lock(cast(LangGraphClient, worker), "C1", "1.0"):
            active += 1
            assert active == 1
            assert lock_id in client.threads.lock_ids
            await asyncio.sleep(0.01)
            replies += 1
            active -= 1

    await asyncio.wait_for(asyncio.gather(reply(), reply(), reply()), timeout=1)
    assert replies == 3
    assert not client.threads.lock_ids


@pytest.mark.parametrize("age", [None, 0, 62])
async def test_unexpired_or_undated_lock_is_not_reclaimed(
    monkeypatch: pytest.MonkeyPatch, age: int | None
) -> None:
    client = _Client()
    lock_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "open-swe:slack-thread-lock:C1:1.0"))
    await client.threads.create(thread_id=lock_id)
    client.threads.locks[lock_id] = {"metadata": {}}
    if age is not None:
        client.threads.locks[lock_id]["created_at"] = (
            datetime.now(UTC) - timedelta(seconds=age)
        ).isoformat()
    if age == 0:
        client.threads.locks[lock_id]["metadata"] = {
            "lock_expires_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
        }
    monkeypatch.setattr(slack_client, "_SLACK_THREAD_MUTATION_LOCK_TIMEOUT_SECONDS", 0.1)
    with pytest.raises(TimeoutError, match="Slack thread mutation lock"):
        async with slack_thread_mutation_lock(cast(LangGraphClient, client), "C1", "1.0"):
            pytest.fail("An unexpired lock must block replies")
    assert lock_id in client.threads.lock_ids


@pytest.mark.parametrize("operation", ["get", "delete"])
async def test_disappearing_orphan_retries_acquisition(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    client = _Client()
    lock_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "open-swe:slack-thread-lock:C1:1.0"))
    await client.threads.create(thread_id=lock_id)
    client.threads.locks[lock_id]["created_at"] = (
        datetime.now(UTC) - timedelta(minutes=2)
    ).isoformat()
    original = getattr(client.threads, operation)
    disappeared = False

    async def disappear(thread_id: str) -> object:
        nonlocal disappeared
        if not disappeared:
            disappeared = True
            client.threads.lock_ids.discard(thread_id)
            client.threads.locks.pop(thread_id)
            response = httpx2.Response(404, request=httpx2.Request("GET", "http://test/threads"))
            raise NotFoundError("not found", response=response, body=None)
        return await original(thread_id)

    monkeypatch.setattr(client.threads, operation, disappear)
    async with slack_thread_mutation_lock(cast(LangGraphClient, client), "C1", "1.0"):
        assert lock_id in client.threads.lock_ids
    assert not client.threads.lock_ids


@pytest.mark.parametrize("connection_error", [False, True])
async def test_release_retries_transient_failures_and_allows_next_reply(
    monkeypatch: pytest.MonkeyPatch, connection_error: bool
) -> None:
    client = _Client()
    delete = client.threads.delete
    attempts = 0

    async def flaky_delete(thread_id: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            if connection_error:
                raise APIConnectionError(
                    message="connection failed",
                    request=httpx.Request("DELETE", "http://test/threads"),
                )
            response = httpx2.Response(503, request=httpx2.Request("DELETE", "http://test/threads"))
            raise InternalServerError("unavailable", response=response, body=None)
        await delete(thread_id)

    monkeypatch.setattr(client.threads, "delete", flaky_delete)
    async with slack_thread_mutation_lock(cast(LangGraphClient, client), "C1", "1.0"):
        pass
    assert not client.threads.lock_ids
    assert attempts == 3
    async with slack_thread_mutation_lock(cast(LangGraphClient, client), "C1", "1.0"):
        pass
    assert not client.threads.lock_ids


async def test_release_retry_does_not_delete_replaced_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client()

    async def replaced_delete(thread_id: str) -> None:
        client.threads.locks[thread_id] = {"metadata": {"lock_owner": "successor"}}
        raise httpx2.ConnectError("response lost")

    monkeypatch.setattr(client.threads, "delete", replaced_delete)
    async with slack_thread_mutation_lock(cast(LangGraphClient, client), "C1", "1.0"):
        pass
    assert len(client.threads.locks) == 1
    assert next(iter(client.threads.locks.values())) == {"metadata": {"lock_owner": "successor"}}


@pytest.mark.asyncio
async def test_resolver_creates_a_concurrency_safe_mapping() -> None:
    client: Any = _Client()

    first = await resolve_slack_thread_id(client, "C1", "1.0")
    second = await resolve_slack_thread_id(client, "C1", "1.0")

    assert second == first
    concurrent_client: Any = _Client()
    assert await resolve_slack_thread_id(concurrent_client, "C1", "1.0") == first
    assert await lookup_slack_thread_id(client, "C1", "1.0") == first


@pytest.mark.asyncio
async def test_resolver_backfills_existing_thread_from_metadata() -> None:
    client: Any = _Client([_legacy_thread("legacy-thread")])

    resolved = await resolve_slack_thread_id(client, "C1", "1.0")

    assert resolved == "legacy-thread"
    assert await lookup_slack_thread_id(client, "C1", "1.0") == "legacy-thread"


@pytest.mark.asyncio
async def test_resolver_rejects_ambiguous_legacy_metadata() -> None:
    client: Any = _Client([_legacy_thread("one"), _legacy_thread("two")])

    with pytest.raises(SlackThreadMappingError, match="Multiple"):
        await resolve_slack_thread_id(client, "C1", "1.0")

    assert await lookup_slack_thread_id(client, "C1", "1.0") is None


@pytest.mark.asyncio
async def test_binding_refuses_to_overwrite_another_thread() -> None:
    client: Any = _Client()
    await bind_slack_thread_id(client, "C1", "1.0", "one")

    with pytest.raises(SlackThreadMappingError, match="already mapped"):
        await bind_slack_thread_id(client, "C1", "1.0", "two")


@pytest.mark.asyncio
async def test_message_mapping_uses_executing_run_without_replacing_thread_mapping() -> None:
    client: Any = _Client()
    await store_slack_run_mapping(
        client,
        "C1",
        "1.0",
        "queued-run",
        triggering_user_id="U1",
        agent_thread_id="thread-one",
    )

    await store_slack_message_run_mapping(
        client,
        "C1",
        "1.0",
        "1.1",
        run_id="active-run",
        triggering_user_id="active-user",
    )

    namespace = ("slack_run_map", "C1")
    assert client.store.items[(namespace, "thread:1.0")]["value"]["run_id"] == "queued-run"
    message = client.store.items[(namespace, "message:1.1")]["value"]
    assert message["run_id"] == "active-run"
    assert message["triggering_user_id"] == "active-user"
    assert message["agent_thread_id"] == "thread-one"


@pytest.mark.asyncio
async def test_delete_does_not_remove_a_location_reassigned_to_another_thread() -> None:
    client: Any = _Client()
    await bind_slack_thread_id(client, "C1", "1.0", "new-thread")
    await store_slack_run_mapping(client, "C1", "1.0", "new-run", message_ts="1.1")

    await delete_slack_thread_associations(client, "C1", "1.0", expected_thread_id="old-thread")

    assert await lookup_slack_thread_id(client, "C1", "1.0") == "new-thread"
    remaining = [item["value"] for item in client.store.items.values()]
    assert any(item.get("run_id") == "new-run" for item in remaining)


@pytest.mark.asyncio
async def test_delete_removes_only_exact_slack_location_associations() -> None:
    client: Any = _Client()
    await bind_slack_thread_id(client, "C1", "1.0", "thread-one")
    await bind_slack_thread_id(client, "C1", "2.0", "thread-two")
    await store_slack_run_mapping(client, "C1", "1.0", "run-one", message_ts="1.1")
    await store_slack_run_mapping(client, "C1", "2.0", "run-two", message_ts="2.1")

    await delete_slack_thread_associations(client, "C1", "1.0")

    assert await lookup_slack_thread_id(client, "C1", "1.0") is None
    assert await lookup_slack_thread_id(client, "C1", "2.0") == "thread-two"
    fresh: Any = _Client()
    fresh.store.items = dict(client.store.items)
    replacement = await resolve_slack_thread_id(client, "C1", "1.0")
    assert replacement != "thread-one"
    assert await resolve_slack_thread_id(fresh, "C1", "1.0") == replacement
    remaining = [item["value"] for item in client.store.items.values()]
    assert all(item.get("run_id") != "run-one" for item in remaining)
    assert any(item.get("run_id") == "run-two" for item in remaining)


@pytest.mark.asyncio
async def test_exact_run_mapping_survives_overlapping_thread_runs() -> None:
    client: Any = _Client()
    await store_slack_run_mapping(client, "C1", "1.0", "run-one")
    await store_slack_run_mapping(client, "C1", "1.0", "run-two")

    await store_slack_message_run_mapping(client, "C1", "1.0", "1.1", run_id="run-one")
    await store_slack_message_run_mapping(client, "C1", "1.0", "1.2", run_id="run-two")
    await store_slack_message_run_mapping(client, "C1", "1.0", "1.3", run_id="run-one")

    assert await lookup_slack_run_message_mapping(client, "C1", "run-one") == {
        "run_id": "run-one",
        "thread_ts": "1.0",
        "message_ts": "1.3",
    }
    assert await lookup_slack_run_message_mapping(client, "C1", "run-two") == {
        "run_id": "run-two",
        "thread_ts": "1.0",
        "message_ts": "1.2",
    }


@pytest.mark.asyncio
async def test_message_mapping_tracks_latest_reply_in_exact_run() -> None:
    client: Any = _Client()
    await store_slack_run_mapping(client, "C1", "1.0", "run-one")
    await store_slack_message_run_mapping(client, "C1", "1.0", "1.1", run_id="run-one")
    assert (await lookup_slack_run_message_mapping(client, "C1", "run-one"))["message_ts"] == "1.1"
    await store_slack_run_mapping(client, "C1", "1.0", "run-two")
    await store_slack_message_run_mapping(client, "C1", "1.0", "1.2", run_id="run-two")
    assert (await lookup_slack_run_message_mapping(client, "C1", "run-two"))["message_ts"] == "1.2"
    await store_slack_message_run_mapping(client, "C1", "1.0", "1.3", run_id="run-one")
    assert (await lookup_slack_run_message_mapping(client, "C1", "run-one"))["message_ts"] == "1.3"
