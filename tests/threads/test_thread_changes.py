import asyncio
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from agent.database import postgres
from agent.threads import changes, events, summary
from agent.threads import routes as thread_routes

_ALICE = events.Viewer(login="alice", email="alice@example.com", include_all=False)


def _thread(thread_id: str, **metadata: object) -> dict[str, object]:
    return {
        "thread_id": thread_id,
        "status": "idle",
        "metadata": {"source": "dashboard", "latest_run_status": "success", **metadata},
    }


class _Threads:
    def __init__(self, threads: list[dict[str, object]]) -> None:
        self._threads = {str(thread["thread_id"]): thread for thread in threads}

    async def search(
        self, *, ids: list[str], limit: int, select: list[str]
    ) -> list[dict[str, object]]:
        del limit, select
        return [self._threads[thread_id] for thread_id in ids if thread_id in self._threads]


@pytest.fixture
def pins(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    pinned: list[str] = []

    async def list_pins(login: str) -> list[str]:
        del login
        return list(pinned)

    monkeypatch.setattr(events, "list_thread_pin_ids", list_pins)
    return pinned


@pytest.fixture
def langgraph(monkeypatch: pytest.MonkeyPatch, pins: list[str]):
    del pins

    def install(*threads: dict[str, object]) -> None:
        client = SimpleNamespace(threads=_Threads(list(threads)))
        monkeypatch.setattr(events, "langgraph_client", lambda: client)

    async def no_trace(thread_id: str) -> None:
        del thread_id

    monkeypatch.setattr(summary, "get_langsmith_trace_url", no_trace)
    monkeypatch.delenv("POSTGRES_URI", raising=False)
    return install


@asynccontextmanager
async def _stream(viewer: events.Viewer) -> AsyncIterator[AsyncGenerator[str]]:
    stream = events.stream_thread_changes(viewer)
    try:
        assert await anext(stream) == "event: ready\ndata: {}\n\n"
        yield stream
    finally:
        await stream.aclose()


def _parse(frame: str) -> tuple[str, Mapping[str, object]]:
    event_line, data_line, *_ = frame.split("\n")
    return event_line.removeprefix("event: "), json.loads(data_line.removeprefix("data: "))


async def _next_frame(stream: AsyncGenerator[str]) -> tuple[str, Mapping[str, object]]:
    return _parse(await asyncio.wait_for(anext(stream), timeout=2))


def _updated_id(frame: tuple[str, Mapping[str, object]]) -> object:
    event, data = frame
    assert event == "thread-updated"
    thread = data["thread"]
    assert isinstance(thread, Mapping)
    return thread["id"]


async def test_a_participant_receives_the_page_item_of_a_changed_thread(langgraph) -> None:
    langgraph(_thread("t-1", participant_logins={"alice": True}, title="Fix it"))
    async with _stream(_ALICE) as stream:
        await changes.publish_thread_changed("t-1")
        event, data = await _next_frame(stream)
    assert event == "thread-updated"
    thread = data["thread"]
    assert isinstance(thread, Mapping)
    assert (thread["id"], thread["title"], thread["status"]) == ("t-1", "Fix it", "finished")


async def test_a_thread_the_viewer_cannot_list_is_never_named(langgraph) -> None:
    langgraph(
        _thread("someone-elses", participant_logins={"bob": True}),
        _thread("private", participant_logins={"alice": True}, visibility="private"),
        _thread("oswe-question", participant_logins={"alice": True}, unlisted=True),
        _thread("mine", participant_logins={"alice": True}),
    )
    async with _stream(_ALICE) as stream:
        await changes.publish_thread_changed("someone-elses")
        await changes.publish_thread_changed("private")
        await changes.publish_thread_changed("oswe-question")
        await changes.publish_thread_changed("gone")
        await changes.publish_thread_changed("mine")
        assert _updated_id(await _next_frame(stream)) == "mine"


async def test_a_private_thread_reaches_its_owner(langgraph) -> None:
    langgraph(
        _thread(
            "private",
            participant_logins={"alice": True, "bob": True},
            owner_login="bob",
            visibility="private",
        )
    )
    bob = events.Viewer(login="bob", email=None, include_all=False)
    async with _stream(bob) as stream:
        await changes.publish_thread_changed("private")
        assert _updated_id(await _next_frame(stream)) == "private"


async def test_an_automation_thread_reaches_a_viewer_who_is_not_a_participant(langgraph) -> None:
    langgraph(_thread("nightly", source="schedule", schedule_id="s-1"))
    async with _stream(_ALICE) as stream:
        await changes.publish_thread_changed("nightly")
        assert _updated_id(await _next_frame(stream)) == "nightly"


async def test_a_thread_pinned_after_the_stream_opened_reaches_its_pinner(
    langgraph, pins: list[str]
) -> None:
    langgraph(_thread("bobs", participant_logins={"bob": True}))
    async with _stream(_ALICE) as stream:
        pins.append("bobs")
        await changes.publish_thread_changed("bobs")
        assert _updated_id(await _next_frame(stream)) == "bobs"


async def test_a_resync_marker_tells_the_client_to_refetch(langgraph) -> None:
    langgraph()
    async with _stream(_ALICE) as stream:
        changes.publish_local(changes.RESYNC)
        assert await _next_frame(stream) == ("resync", {})


async def test_all_threads_require_an_admin() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await thread_routes.api_stream_thread_changes(
            all=True, session={"sub": "alice", "email": "alice@example.com"}
        )
    assert exc_info.value.status_code == 403


async def test_a_postgres_failure_still_reaches_local_subscribers(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    @asynccontextmanager
    async def broken_transaction() -> AsyncIterator[None]:
        raise ConnectionError("database is down")
        yield

    monkeypatch.setattr(postgres, "configured", lambda: True)
    monkeypatch.setattr(postgres, "transaction", broken_transaction)
    async with changes.subscribe() as thread_ids:
        with caplog.at_level(logging.WARNING, logger=changes.__name__):
            await changes.publish_thread_changed("t-1")
        assert await asyncio.wait_for(anext(thread_ids), timeout=1) == "t-1"
    assert any(getattr(record, "thread_id", None) == "t-1" for record in caplog.records)


async def test_an_overflowing_subscriber_is_told_to_resync_once() -> None:
    async with changes.subscribe() as thread_ids:
        for index in range(changes._QUEUE_LIMIT * 2 + 10):
            changes.publish_local(f"t-{index}")
        received: list[str] = []
        while True:
            try:
                received.append(await asyncio.wait_for(anext(thread_ids), timeout=0.05))
            except TimeoutError:
                break
    assert received[0] == changes.RESYNC
    assert received.count(changes.RESYNC) == 1
    assert received[-1] == f"t-{changes._QUEUE_LIMIT * 2 + 9}"
