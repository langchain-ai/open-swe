import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import aclosing

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from agent.dashboard import oauth, routes
from agent.database import postgres
from agent.review.session import REVIEW_CHAT_SOURCE
from agent.threads import listing, stream
from agent.threads.index import (
    delete_thread_index,
    mark_thread_index_status,
    upsert_thread_index_rows,
)
from agent.threads.index_listener import CHANNEL
from agent.threads.index_query import ThreadListFilters, thread_index_head
from agent.transcript.listener import _dsn
from agent.utils.json_types import JsonObject

BASE_MS = 1_750_000_000_000

type Frame = tuple[str, JsonObject]


@pytest.fixture(autouse=True)
def _index_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THREAD_INDEX_READS", "true")
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")

    def no_langgraph() -> None:
        raise AssertionError("the stream must not call LangGraph")

    monkeypatch.setattr(listing, "langgraph_client", no_langgraph)


def _thread(
    thread_id: str,
    *,
    login: str = "octocat",
    participants: tuple[str, ...] = (),
    **overrides: object,
) -> JsonObject:
    return {
        "thread_id": thread_id,
        "status": "idle",
        "created_at": "2025-06-15T00:00:00+00:00",
        "updated_at": "2025-06-15T00:00:00+00:00",
        "metadata": {
            "source": "dashboard",
            "owner_login": login,
            "visibility": "public",
            "thread_category": "interactive",
            "participant_logins": dict.fromkeys((login, *participants), True),
            "title": f"Thread {thread_id}",
            "latest_run_status": "success",
            "created_at_ms": BASE_MS,
            "updated_at_ms": BASE_MS,
            **overrides,
        },
    }


def _viewer(login: str = "octocat", *, include_all: bool = False) -> ThreadListFilters:
    return ThreadListFilters(
        login=login, include_all=include_all, resolved=False, scope="interactive"
    )


def _parse(chunk: str) -> Frame:
    event, data = chunk.strip().split("\n")
    return event.removeprefix("event: "), json.loads(data.removeprefix("data: "))


async def _next(frames: AsyncGenerator[str]) -> Frame:
    async with asyncio.timeout(5):
        while True:
            chunk = await anext(frames)
            if not chunk.startswith(":"):
                return _parse(chunk)


def _thread_id(frame: Frame) -> str:
    event, data = frame
    if event == "thread-upserted":
        thread = data["thread"]
        assert isinstance(thread, dict)
        return str(thread["id"])
    return str(data["thread_id"])


@pytest.mark.usefixtures("registry_db")
async def test_replay_sends_only_the_viewers_rows_after_the_cursor() -> None:
    await upsert_thread_index_rows([_thread("before")])
    after = await thread_index_head()
    await upsert_thread_index_rows(
        [_thread("mine"), _thread("theirs", login="someone-else"), _thread("before")]
    )

    async with aclosing(stream.stream_thread_index(_viewer(), after)) as frames:
        first, second = await _next(frames), await _next(frames)
        third = await _next(frames)

    assert first[0] == second[0] == "thread-upserted"
    assert {_thread_id(first), _thread_id(second)} == {"mine", "before"}
    assert first[1]["seq"] > after
    assert third == ("synchronized", {"seq": await thread_index_head()})


@pytest.mark.usefixtures("registry_db")
async def test_replay_longer_than_the_cap_asks_for_a_resync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stream, "REPLAY_LIMIT", 2)
    await upsert_thread_index_rows([_thread("first")])
    after = await thread_index_head()
    await upsert_thread_index_rows([_thread(f"later-{index}") for index in range(3)])
    head = await thread_index_head()

    async with aclosing(stream.stream_thread_index(_viewer(), after)) as frames:
        assert await _next(frames) == ("resync", {"seq": head})
        assert await _next(frames) == ("synchronized", {"seq": head})


@pytest.mark.usefixtures("registry_db")
async def test_resolving_a_thread_removes_it_and_unresolving_restores_it() -> None:
    await upsert_thread_index_rows([_thread("chat")])

    async with aclosing(stream.stream_thread_index(_viewer(), 0)) as frames:
        assert (await _next(frames))[0] == "synchronized"
        await upsert_thread_index_rows([_thread("chat", resolved=True)])
        event, data = await _next(frames)
        assert (event, data["thread_id"], data["reason"]) == ("thread-removed", "chat", "filtered")
        await upsert_thread_index_rows([_thread("chat")])
        upserted = await _next(frames)
        assert (upserted[0], _thread_id(upserted)) == ("thread-upserted", "chat")


@pytest.mark.usefixtures("registry_db")
async def test_a_thread_flipped_private_leaves_other_participants_but_not_its_owner() -> None:
    shared = _thread("shared", login="owner", participants=("octocat",))
    await upsert_thread_index_rows([shared])

    async with (
        aclosing(stream.stream_thread_index(_viewer("octocat"), 0)) as participant,
        aclosing(stream.stream_thread_index(_viewer("owner"), 0)) as owner,
    ):
        assert (await _next(participant))[0] == "synchronized"
        assert (await _next(owner))[0] == "synchronized"
        await upsert_thread_index_rows(
            [_thread("shared", login="owner", participants=("octocat",), visibility="private")]
        )
        event, data = await _next(participant)
        assert (event, data["reason"]) == ("thread-removed", "unreadable")
        upserted = await _next(owner)
        assert (upserted[0], _thread_id(upserted)) == ("thread-upserted", "shared")


@pytest.mark.usefixtures("registry_db")
async def test_an_admin_never_hears_of_someone_elses_review_chat() -> None:
    review = _thread(
        "review",
        login="reviewer",
        source=REVIEW_CHAT_SOURCE,
        github_login="reviewer",
        repo_owner="acme",
        repo_name="api",
        pr_number=7,
        thread_category="review",
    )

    async with aclosing(
        stream.stream_thread_index(_viewer("admin", include_all=True), 0)
    ) as frames:
        assert (await _next(frames))[0] == "synchronized"
        await upsert_thread_index_rows([review])
        await delete_thread_index("unrelated-missing")
        await upsert_thread_index_rows([_thread("sentinel", login="alice")])
        assert _thread_id(await _next(frames)) == "sentinel"


@pytest.mark.usefixtures("registry_db")
async def test_a_deleted_thread_is_removed() -> None:
    await upsert_thread_index_rows([_thread("doomed")])

    async with aclosing(stream.stream_thread_index(_viewer(), 0)) as frames:
        assert (await _next(frames))[0] == "synchronized"
        assert await delete_thread_index("doomed")
        assert await _next(frames) == (
            "thread-removed",
            {"seq": None, "thread_id": "doomed", "reason": "deleted"},
        )


@pytest.mark.usefixtures("registry_db")
async def test_a_turn_status_change_notifies_once_it_commits() -> None:
    await upsert_thread_index_rows([_thread("busy")])
    payloads: asyncio.Queue[str] = asyncio.Queue()
    connection = await asyncpg.connect(dsn=_dsn())
    try:
        await connection.add_listener(CHANNEL, lambda *args: payloads.put_nowait(args[-1]))
        async with postgres.transaction() as conn:
            await mark_thread_index_status(conn, "busy", status="running", run_id="run-2")
        async with asyncio.timeout(5):
            payload = await payloads.get()
    finally:
        await connection.close()
    assert payload == f"busy:{await thread_index_head()}"


def _app(session: dict[str, str]) -> FastAPI:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[oauth.require_session] = lambda: session
    return app


async def _status(app: FastAPI, query: str) -> int:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/dashboard/api/threads/index/events{query}")
        return response.status_code


async def test_the_stream_is_unavailable_without_index_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("THREAD_INDEX_READS", "false")
    assert await _status(_app({"sub": "octocat"}), "") == 404


async def test_the_all_view_is_for_admins_only() -> None:
    assert await _status(_app({"sub": "octocat"}), "?all=true") == 403
