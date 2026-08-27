import asyncio
from typing import Any

import httpx
import pytest
from langgraph_sdk.errors import ConflictError

from agent.utils.slack import (
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

    async def create(self, *, thread_id: str, **_kwargs: Any) -> None:
        if thread_id in self.lock_ids:
            response = httpx.Response(409, request=httpx.Request("POST", "http://test/threads"))
            raise ConflictError("already exists", response=response, body=None)
        self.lock_ids.add(thread_id)

    async def delete(self, thread_id: str) -> None:
        self.lock_ids.discard(thread_id)

    async def search(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.matches

    async def get(self, thread_id: str) -> dict[str, Any]:
        return next(match for match in self.matches if match.get("thread_id") == thread_id)


class _Client:
    def __init__(self, matches: list[dict[str, Any]] | None = None) -> None:
        self.store = _Store()
        self.threads = _Threads(matches)


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
