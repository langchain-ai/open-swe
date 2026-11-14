import asyncio
import json
from contextlib import aclosing
from types import SimpleNamespace
from uuid import uuid7

import pytest
from sqlalchemy import text

from openswe.database import postgres
from openswe.database.notifications import LISTENER
from openswe.plugins.records import DocumentScope
from openswe.ui_invalidations import Topic, outbox
from openswe.ui_invalidations.hub import HUB, Stream
from openswe.ui_invalidations.routes import _stream
from openswe.ui_invalidations.topics import KeyedTopic
from openswe.workspaces.store import DEFAULT_WORKSPACE_SLUG, WORKSPACES


def test_a_plugins_document_topics_can_always_be_subscribed_to() -> None:
    # The longest names GitHub allows, and the "_" a repository and a collection may hold:
    # one malformed topic fails the whole subscription.
    plugin = uuid7()
    for scope in (
        DocumentScope(repository=f"{'o' * 39}/{'r_' * 50}"),
        DocumentScope(user_id=uuid7()),
    ):
        key = scope.topic_key(plugin, f"my_{'c' * 61}")
        assert Topic.well_formed(Topic.PLUGIN_DOCUMENTS.keyed(key))


@pytest.mark.parametrize("topic", [Topic.PULL_REQUEST_CHECKS, Topic.THREADS])
async def test_a_burst_of_changes_reaches_a_reader_as_its_first_and_last(
    monkeypatch: pytest.MonkeyPatch,
    topic: KeyedTopic[str],
) -> None:
    monkeypatch.setattr(topic, "min_interval_seconds", 0.3)
    checks = topic.keyed("public" if topic is Topic.THREADS else "acme/app/7")
    review = Topic.PULL_REQUESTS.keyed("acme/app/7")
    stream = Stream(frozenset({checks, review}))

    stream.mark([checks])
    assert await stream.drain(1) == {checks}

    stream.mark([checks])
    stream.mark([checks, review])
    assert await stream.drain(1) == {review}
    assert await stream.drain(0.05) == set()

    loop = asyncio.get_running_loop()
    started = loop.time()
    assert await stream.drain(1) == {checks}
    assert loop.time() - started > 0.1

    # A reconnect replays the change the previous stream just sent; the reader
    # still gets it, but no sooner than the interval after its data.
    reconnected = Stream(frozenset({checks}))
    reconnected.hold_fresh({checks: 0})
    reconnected.mark([checks])
    assert reconnected.take() == set()
    started = loop.time()
    assert await reconnected.drain(1) == {checks}
    assert loop.time() - started > 0.2


def _parse(frame: str) -> tuple[str, dict[str, object]]:
    event, data = frame.strip().split("\n")
    return event.removeprefix("event: "), json.loads(data.removeprefix("data: "))


async def test_a_reconnecting_reader_is_told_only_what_committed_within_its_window(
    registry_db: None,
) -> None:
    workspaces = Topic.WORKSPACES.name
    async with postgres.transaction() as conn:
        await Topic.WORKSPACES.invalidate(conn)
    try:
        async with postgres.transaction() as conn:
            await Topic.REVIEW_STYLES.invalidate(conn)
            raise RuntimeError("abort")
    except RuntimeError:
        pass

    assert await outbox.invalidated_since({workspaces: 5, Topic.REVIEW_STYLES.name: 5}) == {
        workspaces
    }

    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE ui_invalidation SET created_at = created_at - interval '10 minutes'")
        )
    assert await outbox.invalidated_since({workspaces: 60}) == set()
    assert await outbox.invalidated_since({workspaces: 3600}) == {workspaces}

    assert await outbox.prune() == 0
    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE ui_invalidation SET created_at = created_at - interval '2 days'")
        )
    assert await outbox.prune() == 1


async def test_only_readers_of_a_private_thread_hear_its_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = {"source": "dashboard", "visibility": "private", "owner_login": "alice"}
    threads = {"t-private": private, "t-shared": {"source": "dashboard"}}

    async def get(thread_id: str) -> dict[str, object]:
        return {"thread_id": thread_id, "metadata": threads[thread_id]}

    client = SimpleNamespace(threads=SimpleNamespace(get=get))
    monkeypatch.setattr("openswe.utils.thread_ops.langgraph_client", lambda: client)
    topics = [Topic.THREAD_QUEUES.keyed(thread_id) for thread_id in threads]

    assert await Topic.audible({"sub": "alice"}, topics) == set(topics)
    assert await Topic.audible({"sub": "bob"}, topics) == {"thread-queues/t-shared"}


@pytest.mark.parametrize("visibility", ["private", "public"])
async def test_private_activity_only_reaches_its_owner(
    registry_db: None, monkeypatch: pytest.MonkeyPatch, visibility: str
) -> None:
    owner, other = uuid7(), uuid7()

    async def user(self: object) -> SimpleNamespace:
        return SimpleNamespace(id=owner)

    monkeypatch.setattr("openswe.threads.identity.ThreadIdentity.user", user)
    async with postgres.transaction() as conn:
        await Topic.THREADS.activity(conn, {"visibility": visibility, "owner_login": "alice"})
    topics = {Topic.THREADS.name, Topic.THREADS.keyed("public"), Topic.THREADS.keyed(str(owner))}
    assert await outbox.invalidated_since(dict.fromkeys(topics, 60)) == {
        Topic.THREADS.keyed(str(owner))
    } | ({Topic.THREADS.keyed("public")} if visibility == "public" else set())
    assert await Topic.audible({"sub": "bob", "user_id": str(other)}, topics) == {
        Topic.THREADS.keyed("public")
    }
    assert await Topic.audible({"sub": "alice", "user_id": str(owner)}, topics) == topics - {
        Topic.THREADS.name
    }
    assert await Topic.audible(
        {"sub": "alice", "user_id": str(owner), "provider": "langsmith"}, topics
    ) == {Topic.THREADS.keyed(str(owner))}


async def test_a_workspace_write_reaches_an_open_stream(registry_db: None) -> None:
    await HUB.start()
    LISTENER.start()
    try:
        async with asyncio.timeout(10):
            while not LISTENER.connected:
                await asyncio.sleep(0.05)
            async with aclosing(_stream({Topic.WORKSPACES.name: 0}, [])) as frames:
                event, hello = _parse(await anext(frames))
                assert (event, hello["invalidated"], hello["denied"]) == ("hello", [], [])

                await WORKSPACES.mark_refresh_builder(DEFAULT_WORKSPACE_SLUG, None)

                assert _parse(await anext(frames)) == (
                    "invalidated",
                    {"topics": [Topic.WORKSPACES.name]},
                )
    finally:
        await HUB.stop()
        await LISTENER.stop()
