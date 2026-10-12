import asyncio
import json
from collections.abc import AsyncIterator
from typing import cast
from unittest.mock import AsyncMock

import httpx
import pytest
from langgraph_sdk.client import LangGraphClient

from openswe.threads import listing, pins, summary
from tests.conftest import FakeStore


class ThreadAPI:
    def __init__(self) -> None:
        self.threads: dict[str, dict[str, object]] = {}
        self.run_statuses: dict[str, str] = {}
        self.requests: list[httpx.Request] = []
        self.active_runs = 0
        self.peak_runs = 0
        self.fail_search = False

    def add(self, thread_id: str, **metadata: object) -> None:
        self.threads[thread_id] = {
            "thread_id": thread_id,
            "status": "idle",
            "metadata": {
                "source": "dashboard",
                "title": thread_id,
                "latest_run_status": "success",
                **metadata,
            },
            "created_at": "2026-09-01T00:00:00Z",
            "updated_at": "2026-09-02T00:00:00Z",
            "values": {"messages": ["large conversation" * 1000]},
        }

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/threads/search":
            if self.fail_search:
                return httpx.Response(503, json={"detail": "unavailable"})
            body = cast(dict[str, object], json.loads(request.content))
            ids = cast(list[str], body["ids"])
            select = cast(list[str], body["select"])
            assert "values" not in select
            assert set(body) == {"ids", "select", "limit", "offset"}
            # Deliberately use a different order, as the real search API does.
            records = [self.threads[key] for key in reversed(ids) if key in self.threads]
            limit = cast(int, body["limit"])
            return httpx.Response(
                200, json=[{key: record[key] for key in select} for record in records[:limit]]
            )
        _, _, thread_id, *tail = request.url.path.split("/")
        if tail == ["runs"]:
            assert request.url.params["limit"] == "1"
            self.active_runs += 1
            self.peak_runs = max(self.peak_runs, self.active_runs)
            await asyncio.sleep(0)
            self.active_runs -= 1
            status = self.run_statuses.get(thread_id, "running")
            return httpx.Response(200, json=[{"run_id": thread_id, "status": status}])
        assert request.method == "PATCH", "Pinned polling must never GET full threads"
        assert request.headers["Prefer"] == "return=minimal"
        body = cast(dict[str, object], json.loads(request.content))
        metadata = cast(dict[str, object], self.threads[thread_id]["metadata"])
        metadata.update(cast(dict[str, object], body["metadata"]))
        return httpx.Response(204)

    def count(self, method: str, suffix: str) -> int:
        return sum(r.method == method and r.url.path.endswith(suffix) for r in self.requests)


@pytest.fixture
async def thread_api(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[ThreadAPI]:
    api = ThreadAPI()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(api.handle), base_url="http://langgraph.test"
    ) as http:
        client = LangGraphClient(http)
        monkeypatch.setattr(listing, "langgraph_client", lambda: client)
        monkeypatch.setattr(summary, "get_langsmith_trace_url", AsyncMock(return_value=None))
        monkeypatch.setattr(summary, "is_admin", lambda email, *, login: email == "admin@test")
        yield api


async def test_deleted_unreadable_and_private_pins_are_checked_for_each_viewer_and_refresh(
    thread_api: ThreadAPI, fake_store: FakeStore
) -> None:
    thread_api.add("shared", source="slack", github_login="someone-else")
    thread_api.add("private", visibility="private", owner_login="alice")
    thread_api.add("internal", source="reviewer")
    for login in ("alice", "bob", "admin"):
        for thread_id in ("shared", "private", "internal", "deleted"):
            await pins.pin_thread(login, thread_id)
    for login, email, expected in (
        ("alice", None, ["shared", "private"]),
        ("bob", None, ["shared"]),
        ("admin", "admin@test", ["shared", "private"]),
    ):
        result = await listing.list_dashboard_pinned_threads(login, email=email)
        assert [item["id"] for item in result] == expected
    thread_api.add("shared", visibility="private", owner_login="someone-else")
    del thread_api.threads["private"]
    assert await listing.list_dashboard_pinned_threads("alice") == []
    assert await pins.list_thread_pin_ids("alice") == ["shared", "private", "internal", "deleted"]


async def test_search_failure_propagates_instead_of_returning_an_empty_pin_list(
    thread_api: ThreadAPI, fake_store: FakeStore
) -> None:
    await pins.pin_thread("alice", "pin")
    thread_api.fail_search = True
    with pytest.raises(httpx.HTTPStatusError):
        await listing.list_dashboard_pinned_threads("alice")
    assert await pins.list_thread_pin_ids("alice") == ["pin"]


async def test_latest_run_requests_are_bounded_and_legacy_idle_status_is_refreshed(
    thread_api: ThreadAPI, fake_store: FakeStore
) -> None:
    for index in range(20):
        thread_id = str(index)
        thread_api.add(thread_id, latest_run_status=None)
        await pins.pin_thread("alice", thread_id)
    result = await listing.list_dashboard_pinned_threads("alice")
    assert all(item["status"] == "running" for item in result)
    assert thread_api.count("POST", "/search") == 1
    assert thread_api.count("GET", "/runs") == 20
    assert 1 < thread_api.peak_runs <= 8
