import asyncio
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openswe import event_claims
from openswe.database import postgres
from openswe.slack import client as slack_client
from openswe.slack.channels import SlackChannel
from openswe.slack.client import SlackThreadMappingError, slack_thread_mutation_lock
from openswe.slack.records import (
    bind_slack_thread_id,
    delete_slack_thread_associations,
    lookup_slack_run_mapping,
    lookup_slack_run_message_mapping,
    lookup_slack_thread_id,
    lookup_slack_thread_run_mapping,
    resolve_slack_thread_id,
    store_slack_message_run_mapping,
    store_slack_run_mapping,
)


class _Threads:
    def __init__(self, matches: list[dict[str, Any]] | None = None) -> None:
        self.matches = matches or []

    async def search(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.matches

    async def get(self, thread_id: str) -> dict[str, Any]:
        return next(match for match in self.matches if match.get("thread_id") == thread_id)


class _Client:
    def __init__(self, matches: list[dict[str, Any]] | None = None) -> None:
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
@pytest.mark.usefixtures("registry_db")
async def test_thread_mutation_lock_returns_active_thread() -> None:
    client: Any = _Client(matches=[_legacy_thread("agent-thread")])

    async with slack_thread_mutation_lock(client, "C1", "1.0", thread_id="agent-thread") as active:
        assert active == {"channel_id": "C1", "thread_ts": "1.0"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
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
@pytest.mark.usefixtures("registry_db")
async def test_thread_mutation_lock_lapses_when_its_holder_cannot_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client: Any = _Client()
    monkeypatch.setattr(slack_client, "_SLACK_THREAD_MUTATION_LOCK_TTL", timedelta(seconds=0.5))
    monkeypatch.setattr(event_claims, "release", AsyncMock(side_effect=RuntimeError("503")))

    async with slack_thread_mutation_lock(client, "C1", "1.0"):
        pass

    async with asyncio.timeout(3):
        async with slack_thread_mutation_lock(client, "C1", "1.0"):
            pass


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_resolver_creates_a_concurrency_safe_mapping() -> None:
    client: Any = _Client()

    first = await resolve_slack_thread_id(client, "C1", "1.0")
    second = await resolve_slack_thread_id(client, "C1", "1.0")

    assert second == first
    assert await lookup_slack_thread_id("C1", "1.0") == first


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_resolver_backfills_existing_thread_from_metadata() -> None:
    client: Any = _Client([_legacy_thread("legacy-thread")])

    resolved = await resolve_slack_thread_id(client, "C1", "1.0")

    assert resolved == "legacy-thread"
    assert await lookup_slack_thread_id("C1", "1.0") == "legacy-thread"


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_resolver_rejects_ambiguous_legacy_metadata() -> None:
    client: Any = _Client([_legacy_thread("one"), _legacy_thread("two")])

    with pytest.raises(SlackThreadMappingError, match="Multiple"):
        await resolve_slack_thread_id(client, "C1", "1.0")

    assert await lookup_slack_thread_id("C1", "1.0") is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_binding_refuses_to_overwrite_another_thread() -> None:
    await bind_slack_thread_id("C1", "1.0", "one")

    with pytest.raises(SlackThreadMappingError, match="already mapped"):
        await bind_slack_thread_id("C1", "1.0", "two")
    assert await lookup_slack_thread_id("C1", "1.0") == "one"


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_writes_add_a_stale_stub_only_for_an_unseen_channel() -> None:
    await SlackChannel.save_all({"id": "C2", "name": "known"})
    await bind_slack_thread_id("C1", "1.0", "one")
    await bind_slack_thread_id("C2", "1.0", "two")

    async with postgres.session() as session:
        stub = await session.get(SlackChannel, "C1")
        known = await session.get(SlackChannel, "C2")
    assert stub is not None and not stub.fresh and stub.name == ""
    assert known is not None and known.fresh and known.name == "known"


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_message_mapping_uses_executing_run_without_replacing_thread_mapping() -> None:
    await store_slack_run_mapping(
        "C1", "1.0", "queued-run", triggering_user_id="U1", agent_thread_id="thread-one"
    )

    await store_slack_message_run_mapping(
        "C1", "1.0", "1.1", run_id="active-run", triggering_user_id="active-user"
    )

    current = await lookup_slack_thread_run_mapping("C1", "1.0")
    assert current is not None and current.run_id == "queued-run"
    message = await lookup_slack_run_mapping("C1", "1.1")
    assert message is not None
    assert message.run_id == "active-run"
    assert message.triggering_user_id == "active-user"
    assert message.agent_thread_id == "thread-one"


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_delete_does_not_remove_a_location_reassigned_to_another_thread() -> None:
    await bind_slack_thread_id("C1", "1.0", "new-thread")
    await store_slack_run_mapping("C1", "1.0", "new-run", message_ts="1.1")

    await delete_slack_thread_associations("C1", "1.0", expected_thread_id="old-thread")

    assert await lookup_slack_thread_id("C1", "1.0") == "new-thread"
    assert await lookup_slack_run_message_mapping("C1", "new-run") is not None


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_delete_removes_only_exact_slack_location_associations() -> None:
    client: Any = _Client()
    await bind_slack_thread_id("C1", "1.0", "thread-one")
    await bind_slack_thread_id("C1", "2.0", "thread-two")
    await store_slack_run_mapping("C1", "1.0", "run-one", message_ts="1.1")
    await store_slack_run_mapping("C1", "2.0", "run-two", message_ts="2.1")

    await delete_slack_thread_associations("C1", "1.0")

    assert await lookup_slack_thread_id("C1", "1.0") is None
    assert await lookup_slack_thread_id("C1", "2.0") == "thread-two"
    replacement = await resolve_slack_thread_id(client, "C1", "1.0")
    assert replacement != "thread-one"
    assert await resolve_slack_thread_id(client, "C1", "1.0") == replacement
    assert await lookup_slack_run_message_mapping("C1", "run-one") is None
    assert await lookup_slack_run_mapping("C1", "1.1") is None
    assert await lookup_slack_run_message_mapping("C1", "run-two") is not None
    assert await lookup_slack_run_mapping("C1", "2.1") is not None


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_message_mapping_tracks_latest_reply_in_exact_run() -> None:
    await store_slack_run_mapping("C1", "1.0", "run-one")
    await store_slack_run_mapping("C1", "1.0", "run-two")

    await store_slack_message_run_mapping("C1", "1.0", "1.1", run_id="run-one")
    await store_slack_message_run_mapping("C1", "1.0", "1.2", run_id="run-two")
    await store_slack_message_run_mapping("C1", "1.0", "1.3", run_id="run-one")

    run_one = await lookup_slack_run_message_mapping("C1", "run-one")
    run_two = await lookup_slack_run_message_mapping("C1", "run-two")
    assert run_one is not None and (run_one.thread_ts, run_one.message_ts) == ("1.0", "1.3")
    assert run_two is not None and (run_two.thread_ts, run_two.message_ts) == ("1.0", "1.2")
    current = await lookup_slack_thread_run_mapping("C1", "1.0")
    assert current is not None and current.run_id == "run-two"
