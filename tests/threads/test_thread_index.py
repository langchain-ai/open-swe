import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from openswe.threads import index
from openswe.threads.index import IndexedThread, ThreadExistsError, ThreadSearch

pytestmark = pytest.mark.usefixtures("registry_db")

_DAY = datetime(2026, 10, 1, tzinfo=UTC)


async def _seed() -> None:
    for n, (owner, visibility, status) in enumerate(
        [("ada", "private", "idle"), ("ada", "public", "busy"), ("bob", "public", "idle")]
    ):
        await index.create(
            f"t{n}",
            runtime="mda" if n else "local",
            metadata={"owner_login": owner, "visibility": visibility, "repo": {"name": "app"}},
            status=status,
            created_at=_DAY + timedelta(hours=n),
            updated_at=_DAY + timedelta(hours=10 - n),
        )


async def test_a_thread_round_trips_in_the_agent_servers_shape() -> None:
    created = await index.create("t1", runtime="mda", metadata={"title": "Fix it"})

    assert await index.get("t1") == created
    shape = created.as_langgraph()
    assert shape["thread_id"] == "t1"
    assert shape["metadata"] == {"title": "Fix it"}
    assert shape["status"] == "idle"
    assert created.as_langgraph(["thread_id", "status"]) == {"thread_id": "t1", "status": "idle"}


async def test_creating_an_indexed_thread_raises_or_returns_it() -> None:
    first = await index.create("t1", runtime="local", metadata={"title": "first"})

    with pytest.raises(ThreadExistsError):
        await index.create("t1", runtime="mda", metadata={"title": "second"})
    assert await index.create("t1", runtime="mda", if_exists="do_nothing") == first


async def test_an_update_merges_top_level_keys_as_the_agent_server_does() -> None:
    await index.create("t1", runtime="mda", metadata={"a": 1, "b": {"x": 1}})

    updated = await index.update("t1", {"b": {"y": 2}, "c": None})

    assert updated is not None
    assert updated.metadata == {"a": 1, "b": {"y": 2}, "c": None}
    assert await index.update("missing", {"a": 1}) is None


async def test_search_filters_sorts_and_pages_like_the_agent_server() -> None:
    await _seed()

    def ids(found: list[index.IndexedThread]) -> list[str]:
        return [thread.thread_id for thread in found]

    assert ids(await ThreadSearch(metadata={"owner_login": "ada"}).run()) == ["t1", "t0"]
    assert ids(await ThreadSearch(metadata={"repo": {"name": "app"}}, limit=2).run()) == [
        "t2",
        "t1",
    ]
    assert ids(await ThreadSearch(sort_by="updated_at", offset=1, limit=5).run()) == ["t1", "t2"]
    assert ids(await ThreadSearch(sort_by="created_at", sort_order="asc").run()) == [
        "t0",
        "t1",
        "t2",
    ]
    assert ids(await ThreadSearch(status="busy").run()) == ["t1"]
    assert ids(await ThreadSearch(ids=["t2", "t0", "nope"]).run()) == ["t2", "t0"]
    assert ids(await ThreadSearch(metadata={"visibility": "public"}, ids=["t0", "t1"]).run()) == [
        "t1"
    ]
    assert await ThreadSearch(metadata={"owner_login": "ada"}, limit=1).count() == 2


async def test_a_status_change_restarts_its_clock_but_a_repeat_does_not() -> None:
    await index.create("t1", runtime="mda")

    await index.set_status("t1", "busy")
    later = datetime.now(UTC) + timedelta(seconds=1)
    [stuck] = await index.busy_since(later)
    await index.set_status("t1", "busy")

    assert [thread.thread_id for thread in await index.busy_since(later)] == [stuck.thread_id]
    await index.set_status("t1", "idle")
    assert await index.busy_since(later) == []
    assert await index.delete("t1")
    assert not await index.delete("t1")


async def test_a_sync_that_read_an_older_copy_never_lands_after_a_newer_one() -> None:
    agent_server = {"title": "old"}
    first_read, release = asyncio.Event(), asyncio.Event()

    def copy() -> IndexedThread:
        return IndexedThread(
            thread_id="t1",
            runtime="local",
            metadata=dict(agent_server),
            status="idle",
            created_at=_DAY,
            updated_at=_DAY,
        )

    async def slow_read() -> IndexedThread:
        read = copy()
        first_read.set()
        await release.wait()
        return read

    async def read() -> IndexedThread:
        return copy()

    slow = asyncio.create_task(index.sync("t1", slow_read))
    await first_read.wait()
    agent_server["title"] = "new"
    fast = asyncio.create_task(index.sync("t1", read))
    await asyncio.sleep(0.1)
    release.set()
    await asyncio.gather(slow, fast)

    synced = await index.get("t1")
    assert synced is not None and synced.metadata == {"title": "new"}
