"""Keeping ``thread_index`` in step with LangGraph: write-through, turn events, reconciler."""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid7

import httpx2
import pytest
from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.errors import NotFoundError
from sqlalchemy import text

from agent.database import postgres
from agent.threads import index as thread_index
from agent.threads import index_sync
from agent.threads.index import load_thread_index_row, upsert_thread_index
from agent.threads.index_sync import sync_thread_index
from agent.transcript.engine import Command, append
from agent.transcript.events import ThreadCreated, TurnCompleted, TurnStarted
from agent.transcript.rebuild import rebuild_thread_projections
from agent.utils import thread_ops
from agent.utils.json_types import JsonObject

_NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def _not_found() -> NotFoundError:
    response = httpx2.Response(404, request=httpx2.Request("GET", "http://test"))
    return NotFoundError("not found", response=response, body=None)


def _thread(thread_id: str, updated_at: datetime, **metadata: object) -> JsonObject:
    return {
        "thread_id": thread_id,
        "status": "idle",
        "created_at": updated_at.isoformat(),
        "updated_at": updated_at.isoformat(),
        "metadata": {"source": "dashboard", "owner_login": "octocat", **metadata},
    }


class _FakeThreads:
    def __init__(self, threads: list[JsonObject]) -> None:
        self.by_id: dict[str, JsonObject] = {str(t["thread_id"]): t for t in threads}
        self.search_offsets: list[int] = []
        self.updates: list[tuple[str, JsonObject]] = []
        self.before_search: Callable[[int], Awaitable[None]] | None = None

    async def search(
        self,
        *,
        metadata: JsonObject,
        limit: int,
        offset: int,
        sort_by: str,
        sort_order: str,
        select: list[str],
    ) -> list[JsonObject]:
        del metadata, sort_by, sort_order, select
        self.search_offsets.append(offset)
        if self.before_search is not None:
            await self.before_search(offset)
        ordered = sorted(self.by_id.values(), key=lambda t: str(t["updated_at"]), reverse=True)
        return ordered[offset : offset + limit]

    async def get(self, thread_id: str) -> JsonObject:
        if thread_id not in self.by_id:
            raise _not_found()
        return self.by_id[thread_id]

    async def update(
        self, *, thread_id: str, metadata: JsonObject, return_minimal: bool = False
    ) -> JsonObject | None:
        self.updates.append((thread_id, dict(metadata)))
        thread = self.by_id[thread_id]
        merged_metadata = thread["metadata"]
        assert isinstance(merged_metadata, dict)
        thread = {**thread, "metadata": {**merged_metadata, **metadata}}
        self.by_id[thread_id] = thread
        return None if return_minimal else thread


class _FakeRuns:
    def __init__(self, statuses: dict[str, str]) -> None:
        self.statuses = statuses

    async def list(
        self, thread_id: str, *, limit: int, status: str | None = None
    ) -> list[JsonObject]:
        del limit
        run_status = self.statuses.get(thread_id)
        if run_status is None or (status is not None and status != run_status):
            return []
        return [{"run_id": f"run-{thread_id}", "status": run_status}]


class _FakeClient:
    def __init__(self, threads: list[JsonObject], run_statuses: dict[str, str] | None = None):
        self.threads = _FakeThreads(threads)
        self.runs = _FakeRuns(run_statuses or {})


@pytest.fixture
def use_client(monkeypatch: pytest.MonkeyPatch) -> Callable[[_FakeClient], None]:
    def install(client: _FakeClient) -> None:
        monkeypatch.setattr(index_sync, "langgraph_client", lambda: client)
        monkeypatch.setattr(thread_ops, "langgraph_client", lambda: client)

    return install


async def _count_rows() -> int:
    async with postgres.transaction() as conn:
        return (await conn.execute(text("SELECT count(*) FROM thread_index"))).scalar_one()


async def test_empty_index_backfills_every_thread(
    registry_db: None, use_client: Callable[[_FakeClient], None]
) -> None:
    threads = [_thread(f"t-{i:03}", _NOW - timedelta(minutes=i)) for i in range(250)]
    lock = {"thread_id": "lock", "status": "idle", "metadata": {"lock_owner": "x"}}
    client = _FakeClient([*threads, {**lock, "updated_at": _NOW.isoformat()}])
    use_client(client)

    result = await sync_thread_index()

    assert result.full is True
    assert result.upserted == 250
    assert await _count_rows() == 250
    assert await load_thread_index_row("lock") is None


async def test_incremental_walk_stops_below_the_mark(
    registry_db: None, use_client: Callable[[_FakeClient], None]
) -> None:
    threads = [_thread(f"t-{i:03}", _NOW - timedelta(hours=i + 1)) for i in range(300)]
    client = _FakeClient(threads)
    use_client(client)
    await sync_thread_index()

    fresh = _thread("fresh", _NOW, title="New")
    client.threads.by_id["fresh"] = fresh
    client.threads.search_offsets.clear()
    result = await sync_thread_index()

    assert result.full is False
    # The first page holds the new thread; the second is wholly below the mark.
    assert client.threads.search_offsets == [0, 100]
    row = await load_thread_index_row("fresh")
    assert row is not None and row.title == "New"


async def test_walk_keeps_a_row_written_while_it_ran(
    registry_db: None, use_client: Callable[[_FakeClient], None]
) -> None:
    stale = _thread("t-1", _NOW, title="Stale")
    client = _FakeClient([stale])
    use_client(client)

    async def write_through(offset: int) -> None:
        if offset == 0:
            await upsert_thread_index(_thread("t-1", _NOW, title="Fresh"))

    client.threads.before_search = write_through
    await sync_thread_index()

    row = await load_thread_index_row("t-1")
    assert row is not None and row.title == "Fresh"


async def test_running_row_is_settled_once_its_run_ends(
    registry_db: None, use_client: Callable[[_FakeClient], None]
) -> None:
    running = _thread("t-1", _NOW, latest_run_status="running", latest_run_id="run-t-1")
    client = _FakeClient([running], run_statuses={"t-1": "success"})
    use_client(client)

    result = await sync_thread_index()

    assert result.settled == 1
    assert client.threads.updates == [
        ("t-1", {"latest_run_status": "success", "latest_run_id": "run-t-1"})
    ]
    row = await load_thread_index_row("t-1")
    assert row is not None and row.status == "finished"


async def test_live_running_row_is_left_running(
    registry_db: None, use_client: Callable[[_FakeClient], None]
) -> None:
    running = _thread("t-1", _NOW, latest_run_status="running")
    client = _FakeClient([running], run_statuses={"t-1": "running"})
    use_client(client)

    result = await sync_thread_index()

    assert result.settled == 0
    assert client.threads.updates == []
    row = await load_thread_index_row("t-1")
    assert row is not None and row.status == "running"


async def test_row_whose_thread_is_gone_is_deleted(
    registry_db: None, use_client: Callable[[_FakeClient], None]
) -> None:
    kept = _thread("kept", _NOW - timedelta(days=30))
    client = _FakeClient([kept])
    use_client(client)
    await sync_thread_index()
    await upsert_thread_index(_thread("gone", _NOW - timedelta(days=30)))
    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE thread_index SET synced_at = clock_timestamp() - interval '8 days'")
        )

    result = await sync_thread_index()

    assert result.deleted == 1
    assert await load_thread_index_row("gone") is None
    assert await load_thread_index_row("kept") is not None


async def test_update_thread_metadata_indexes_the_merged_thread(registry_db: None) -> None:
    client = _FakeClient([_thread("t-1", _NOW, title="Before")])

    merged = await thread_ops.update_thread_metadata(
        "t-1", {"resolved": True}, client=cast(LangGraphClient, client)
    )

    assert merged["metadata"]["title"] == "Before"
    row = await load_thread_index_row("t-1")
    assert row is not None and row.resolved is True and row.title == "Before"


async def test_update_thread_metadata_survives_an_index_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("POSTGRES_URI", raising=False)
    client = _FakeClient([_thread("t-1", _NOW)])

    async def fail(thread: JsonObject) -> None:
        raise RuntimeError("index down")

    monkeypatch.setattr(thread_index, "upsert_thread_index", fail)
    with caplog.at_level(logging.WARNING):
        merged = await thread_ops.update_thread_metadata(
            "t-1", {"resolved": True}, client=cast(LangGraphClient, client)
        )

    assert merged["metadata"]["resolved"] is True
    assert "Could not update the thread index" in caplog.text


async def test_turn_events_move_the_index_row_and_a_rebuild_leaves_it(
    registry_db: None,
) -> None:
    thread_id = str(uuid7())
    await upsert_thread_index(
        _thread(thread_id, _NOW, latest_run_id="run-0", last_viewed_run_id="run-0")
    )
    turn_id = uuid7()
    await append(
        thread_id,
        [
            Command(
                command_id=f"thread:{thread_id}:created",
                event=ThreadCreated(
                    title="A thread", source="dashboard", owner_login="octocat", metadata={}
                ),
                actor_kind="user",
            ),
            Command(
                command_id=f"turn:{turn_id}:started",
                event=TurnStarted(turn_id=turn_id, run_id="run-1"),
                actor_kind="agent",
                turn_id=turn_id,
            ),
        ],
    )
    started = await load_thread_index_row(thread_id)
    assert started is not None
    assert (started.status, started.latest_run_id, started.viewed) == ("running", "run-1", False)

    await append(
        thread_id,
        [
            Command(
                command_id=f"turn:{turn_id}:completed",
                event=TurnCompleted(turn_id=turn_id),
                actor_kind="agent",
                turn_id=turn_id,
            )
        ],
    )
    finished = await load_thread_index_row(thread_id)
    assert finished is not None and finished.status == "finished"

    await rebuild_thread_projections(thread_id)
    assert await load_thread_index_row(thread_id) == finished
