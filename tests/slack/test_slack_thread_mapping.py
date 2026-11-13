import asyncio
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from openswe import event_claims
from openswe.database import postgres
from openswe.slack import client as slack_client
from openswe.slack import mda_forwarding
from openswe.slack import records as slack_records
from openswe.slack.channels import SlackChannel
from openswe.slack.client import SlackThreadMappingError, slack_thread_mutation_lock
from openswe.slack.payloads import SlackChannelContext
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
from openswe.thread_ids import mda_successor_thread_id
from openswe.threads import index
from openswe.webhooks.common import PROVISIONAL_VISIBILITY_KEY, ThreadRecord


@pytest.mark.parametrize("root_field", ["ts", "thread_ts"])
async def test_reply_permalink_without_thread_timestamp(monkeypatch, root_field: str) -> None:
    lookup = AsyncMock(
        side_effect=lambda channel, ts: "thread-1" if ts == "1788425314.774339" else None
    )
    monkeypatch.setattr(slack_records, "lookup_slack_thread_id", lookup)
    monkeypatch.setattr(slack_records, "lookup_slack_run_mapping", AsyncMock(return_value=None))
    client = AsyncMock()
    client.conversations_replies.return_value = {"messages": [{root_field: "1788425314.774339"}]}
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(slack_records.SlackClient, "bot", lambda: context)

    assert (
        await slack_records.lookup_slack_link_thread_id(
            "https://example.slack.com/archives/C123/p1788431248678809"
        )
        == "thread-1"
    )


async def _index_threads(*threads: dict[str, Any]) -> None:
    """Index threads made before Slack locations had their own table, as the backend
    keeps them: the resolver finds one by its metadata."""
    for thread in threads:
        await index.create(thread["thread_id"], runtime="mda", metadata=thread["metadata"])


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
    await _index_threads(_legacy_thread("agent-thread"))

    async with slack_thread_mutation_lock("C1", "1.0", thread_id="agent-thread") as active:
        assert active == {"channel_id": "C1", "thread_ts": "1.0"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_thread_mutation_lock_serializes_same_thread() -> None:
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def first() -> None:
        async with slack_thread_mutation_lock("C1", "1.0"):
            first_entered.set()
            await release_first.wait()

    async def second() -> None:
        await first_entered.wait()
        async with slack_thread_mutation_lock("C1", "1.0"):
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
    monkeypatch.setattr(slack_client, "_SLACK_THREAD_MUTATION_LOCK_TTL", timedelta(seconds=0.5))
    monkeypatch.setattr(event_claims, "release", AsyncMock(side_effect=RuntimeError("503")))

    async with slack_thread_mutation_lock("C1", "1.0"):
        pass

    async with asyncio.timeout(3):
        async with slack_thread_mutation_lock("C1", "1.0"):
            pass


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_resolver_creates_a_concurrency_safe_mapping() -> None:

    first = await resolve_slack_thread_id("C1", "1.0")
    second = await resolve_slack_thread_id("C1", "1.0")

    assert second == first
    assert await lookup_slack_thread_id("C1", "1.0") == first


@pytest.mark.parametrize(
    ("channel_id", "context", "collaborative"),
    [
        ("C1", SlackChannelContext(is_ext_shared=False, is_pending_ext_shared=False), True),
        ("D1", SlackChannelContext(is_im=True), False),
        ("D1", SlackChannelContext(is_ext_shared=False, is_pending_ext_shared=False), False),
        ("C1", SlackChannelContext(), False),
        ("C1", SlackChannelContext(is_ext_shared=True, is_pending_ext_shared=False), False),
        ("C1", SlackChannelContext(is_ext_shared=False, is_pending_ext_shared=True), False),
    ],
    ids=["internal", "dm", "dm-without-im-flag", "unknown", "external", "pending-external"],
)
@pytest.mark.usefixtures("registry_db")
async def test_a_collaborator_can_prompt_a_claimed_internal_conversation_before_its_first_run(
    channel_id: str, context: SlackChannelContext, collaborative: bool
) -> None:
    thread_id = await mda_forwarding.claim_conversation(
        "A1", "T1", channel_id, "1.0", owner_login="alice", channel_context=context
    )

    thread = await index.get(thread_id)
    assert thread is not None
    record = ThreadRecord(exists=True, metadata=thread.metadata)
    assert not record.started
    assert record.authorize("alice")["owner_login"] == "alice"
    if collaborative:
        assert record.authorize("bob")["visibility"] == "public"
    else:
        with pytest.raises(HTTPException) as exc:
            record.authorize("bob")
        assert exc.value.status_code == 404

    await index.update(thread_id, {"visibility": "private", PROVISIONAL_VISIBILITY_KEY: None})
    await mda_forwarding.claim_conversation(
        "A1", "T1", channel_id, "1.0", owner_login="bob", channel_context=context
    )
    private = await index.get(thread_id)
    assert private is not None
    private_record = ThreadRecord(exists=True, metadata=private.metadata)
    assert private_record.started
    assert private_record.authorize("alice")["visibility"] == "private"
    with pytest.raises(HTTPException) as exc:
        private_record.authorize("bob")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_resolver_backfills_existing_thread_from_metadata() -> None:
    await _index_threads(_legacy_thread("legacy-thread"))

    resolved = await resolve_slack_thread_id("C1", "1.0")

    assert resolved == "legacy-thread"
    assert await lookup_slack_thread_id("C1", "1.0") == "legacy-thread"


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_a_location_on_a_local_thread_moves_to_its_mda_successor() -> None:
    old = "00000000-0000-4000-8000-000000000001"
    await index.create(old, runtime="local")
    await bind_slack_thread_id("C1", "1.0", old)

    first = await resolve_slack_thread_id("C1", "1.0")
    second = await resolve_slack_thread_id("C1", "1.0")

    assert first == second == mda_successor_thread_id(old)
    assert await lookup_slack_thread_id("C1", "1.0") == first


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_resolver_rejects_ambiguous_legacy_metadata() -> None:
    await _index_threads(_legacy_thread("one"), _legacy_thread("two"))

    with pytest.raises(SlackThreadMappingError, match="Multiple"):
        await resolve_slack_thread_id("C1", "1.0")

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
    await bind_slack_thread_id("C1", "1.0", "thread-one")
    await bind_slack_thread_id("C1", "2.0", "thread-two")
    await store_slack_run_mapping("C1", "1.0", "run-one", message_ts="1.1")
    await store_slack_run_mapping("C1", "2.0", "run-two", message_ts="2.1")

    await delete_slack_thread_associations("C1", "1.0")

    assert await lookup_slack_thread_id("C1", "1.0") is None
    assert await lookup_slack_thread_id("C1", "2.0") == "thread-two"
    replacement = await resolve_slack_thread_id("C1", "1.0")
    assert replacement != "thread-one"
    assert await resolve_slack_thread_id("C1", "1.0") == replacement
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
