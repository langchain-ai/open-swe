import asyncio
import json
from contextlib import aclosing
from types import SimpleNamespace
from uuid import uuid7

import pytest
from sqlalchemy import text

from openswe.dashboard.settings import SettingsUpdate, upsert_instance_settings
from openswe.database import postgres
from openswe.database.notifications import LISTENER
from openswe.plugins.records import DocumentScope
from openswe.routing.store import INTEGRATIONS
from openswe.ui_invalidations import Topic, outbox
from openswe.ui_invalidations.hub import HUB, Stream
from openswe.ui_invalidations.routes import _stream
from openswe.ui_invalidations.topics import KeyedTopic


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
    settings = Topic.SETTINGS.name
    async with postgres.transaction() as conn:
        await Topic.SETTINGS.invalidate(conn)
    try:
        async with postgres.transaction() as conn:
            await Topic.REVIEW_STYLES.invalidate(conn)
            raise RuntimeError("abort")
    except RuntimeError:
        pass

    assert await outbox.invalidated_since({settings: 5, Topic.REVIEW_STYLES.name: 5}) == {settings}

    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE ui_invalidation SET created_at = created_at - interval '10 minutes'")
        )
    assert await outbox.invalidated_since({settings: 60}) == set()
    assert await outbox.invalidated_since({settings: 3600}) == {settings}

    assert await outbox.prune() == 0
    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE ui_invalidation SET created_at = created_at - interval '2 days'")
        )
    assert await outbox.prune() == 1


async def test_only_readers_of_a_private_thread_hear_its_queue(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openswe.threads import index, runtimes

    class _NoLegacy:
        async def indexed(self, thread_id: str) -> None:
            return None

    monkeypatch.setattr(runtimes.Runtimes, "local", property(lambda _: _NoLegacy()))
    monkeypatch.setattr(
        "openswe.utils.thread_ops.langgraph_client",
        lambda: (_ for _ in ()).throw(AssertionError("must not call HTTP thread client")),
    )

    await index.create(
        "t-private",
        runtime="mda",
        metadata={"source": "dashboard", "visibility": "private", "owner_login": "alice"},
    )
    await index.create(
        "t-shared",
        runtime="mda",
        metadata={"source": "dashboard", "visibility": "public", "owner_login": "alice"},
    )
    await index.create(
        "t-review-alice",
        runtime="mda",
        metadata={
            "source": "review_chat",
            "github_login": "alice",
            "repo_owner": "acme",
            "repo_name": "app",
            "pr_number": 7,
        },
    )
    await index.create(
        "t-incident",
        runtime="mda",
        metadata={"source": "incidents_agent", "visibility": "public", "owner_type": "system"},
    )

    all_ids = ("t-private", "t-shared", "t-review-alice", "t-incident", "t-missing")
    topics = [Topic.THREAD_QUEUES.keyed(thread_id) for thread_id in all_ids]

    assert await Topic.audible({"sub": "alice"}, topics) == {
        "thread-queues/t-private",
        "thread-queues/t-shared",
        "thread-queues/t-review-alice",
    }
    assert await Topic.audible({"sub": "bob"}, topics) == {"thread-queues/t-shared"}
    # An unlinked LangSmith session cannot read public shared GitHub dashboard threads.
    unlinked = f"user:{uuid7()}"
    assert await Topic.audible({"sub": unlinked, "provider": "langsmith"}, topics) == set()


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


async def test_a_global_settings_write_reaches_an_open_stream(registry_db: None) -> None:
    await HUB.start()
    LISTENER.start()
    try:
        async with asyncio.timeout(10):
            while not LISTENER.connected:
                await asyncio.sleep(0.05)
            topics = (Topic.SETTINGS, Topic.ROUTING)
            async with aclosing(_stream({topic.name: 0 for topic in topics}, [])) as frames:
                event, hello = _parse(await anext(frames))
                assert (event, hello["invalidated"], hello["denied"]) == ("hello", [], [])

                for topic in topics:
                    if topic is Topic.SETTINGS:
                        await upsert_instance_settings(SettingsUpdate(pr_summaries=False))
                    else:
                        await INTEGRATIONS.configure_repository("acme/app", actor=None)

                    assert _parse(await anext(frames)) == (
                        "invalidated",
                        {"topics": [topic.name]},
                    )
    finally:
        await HUB.stop()
        await LISTENER.stop()
