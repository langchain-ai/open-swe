import asyncio
import json
from contextlib import aclosing
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from openswe.database import postgres
from openswe.database.notifications import LISTENER
from openswe.ui_invalidations import Topic, outbox
from openswe.ui_invalidations.hub import HUB
from openswe.ui_invalidations.routes import _stream
from openswe.workspaces.store import DEFAULT_WORKSPACE_SLUG, WORKSPACES


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
