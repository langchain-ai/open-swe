import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from typing import cast
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from langgraph_sdk.client import LangGraphClient

from openswe.dashboard.oauth import require_session
from openswe.projects.store import PROJECTS
from openswe.tasks.store import SidebarTaskMembership
from openswe.threads import index as thread_index
from openswe.threads import listing, pins, routes, summary
from openswe.tools import threads as threads_tool
from openswe.users.models import User

pytestmark = pytest.mark.usefixtures("registry_db")


class ThreadAPI:
    def __init__(self) -> None:
        self.threads: dict[str, dict[str, object]] = {}
        self.run_statuses: dict[str, str] = {}
        self.requests: list[httpx.Request] = []
        self.active_runs = 0
        self.peak_runs = 0
        self.fail_search = False
        self.memberships: dict[str, SidebarTaskMembership] = {}

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
            select = cast(list[str], body["select"])
            assert "values" not in select
            if "metadata" in body:
                matches = await self.indexed_search(thread_index.ThreadSearch.model_validate(body))
                return httpx.Response(
                    200,
                    json=[
                        {
                            key: value
                            for key, value in thread.as_langgraph().items()
                            if key in select
                        }
                        for thread in matches
                    ],
                )
            ids = cast(list[str], body["ids"])
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

    async def indexed_search(
        self, query: thread_index.ThreadSearch
    ) -> list[thread_index.IndexedThread]:
        matches: list[thread_index.IndexedThread] = []
        for thread_id, thread in self.threads.items():
            if query.ids is not None and thread_id not in query.ids:
                continue
            metadata = cast(dict[str, object], thread["metadata"])
            if not self.matches_metadata(metadata, query.metadata):
                continue
            indexed = thread_index.IndexedThread.from_agent_server(thread)
            if indexed is not None:
                matches.append(indexed)
        matches.sort(
            key=lambda thread: (getattr(thread, query.sort_by), thread.thread_id),
            reverse=query.sort_order == "desc",
        )
        return matches[query.offset : query.offset + query.limit]

    def matches_metadata(
        self, actual: Mapping[str, object], expected: Mapping[str, object]
    ) -> bool:
        for key, value in expected.items():
            stored = actual.get(key)
            if isinstance(value, dict):
                if not isinstance(stored, dict) or not self.matches_metadata(stored, value):
                    return False
            elif stored != value:
                return False
        return True

    async def sidebar_memberships(
        self, ids: list[str], *, workers_of: bool = False
    ) -> dict[str, SidebarTaskMembership]:
        return {
            thread_id: membership
            for thread_id, membership in self.memberships.items()
            if (
                membership.role == "worker" and membership.coordinator_thread_id in ids
                if workers_of
                else thread_id in ids
            )
        }

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


@pytest.fixture
async def people(registry_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice,bob,admin")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")
    for external_id, login in enumerate(("alice", "bob", "admin")):
        await User.sign_in("github", str(external_id), login=login)


async def test_deleted_unreadable_and_private_pins_are_checked_for_each_viewer_and_refresh(
    thread_api: ThreadAPI, people: None
) -> None:
    thread_api.add("shared", source="slack", github_login="someone-else")
    thread_api.add("private", visibility="private", owner_login="alice")
    thread_api.add("internal", source="reviewer")
    thread_api.add(
        "concierge",
        source="slack",
        visibility="private",
        owner_login="alice",
        source_context={"slack_thread": {"thread_ts": "0", "channel_context": {"is_im": True}}},
    )
    for login in ("alice", "bob", "admin"):
        for thread_id in ("shared", "private", "internal", "deleted", "concierge"):
            await pins.pin_thread(login, thread_id)
    await pins.THREAD_PINS.reorder("alice", ["private", "deleted", "shared"])
    for login, email, expected in (
        ("alice", None, ["private", "shared"]),
        ("bob", None, ["shared"]),
        ("admin", "admin@test", ["shared", "private"]),
    ):
        result = await listing.list_dashboard_pinned_threads(login, email=email)
        assert [item["id"] for item in result] == expected
    thread_api.add("shared", visibility="private", owner_login="someone-else")
    del thread_api.threads["private"]
    assert await listing.list_dashboard_pinned_threads("alice") == []
    assert await pins.list_thread_pin_ids("alice") == [
        "private",
        "deleted",
        "shared",
        "internal",
        "concierge",
    ]


async def test_search_failure_propagates_instead_of_returning_an_empty_pin_list(
    thread_api: ThreadAPI, people: None
) -> None:
    await pins.pin_thread("alice", "pin")
    thread_api.fail_search = True
    with pytest.raises(httpx.HTTPStatusError):
        await listing.list_dashboard_pinned_threads("alice")
    assert await pins.list_thread_pin_ids("alice") == ["pin"]


async def test_latest_run_requests_are_bounded_and_legacy_idle_status_is_refreshed(
    thread_api: ThreadAPI, people: None
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


async def test_pins_keep_readability(
    thread_api: ThreadAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_api.add("public")
    thread_api.add("mine", visibility="private", owner_login="alice")
    thread_api.add("hidden", visibility="private", owner_login="bob")
    monkeypatch.setattr(
        listing,
        "list_thread_pin_ids",
        AsyncMock(return_value=["public", "mine", "hidden"]),
    )
    result = await listing.list_dashboard_pinned_threads("alice")
    assert [item["id"] for item in result] == ["public", "mine"]


async def test_explicit_pin_order_is_personal_and_reconciles_membership(people: None) -> None:
    for thread_id in ("first", "second", "third"):
        await pins.pin_thread("alice", thread_id)
    await pins.pin_thread("bob", "second")
    await pins.pin_thread("bob", "first")
    await pins.THREAD_PINS.reorder("alice", ["second", "first"])
    await pins.pin_thread("alice", "new")
    assert await pins.ThreadPins().ordered_ids("alice") == ["second", "first", "third", "new"]
    await pins.unpin_thread("alice", "second")
    await pins.pin_thread("alice", "second")
    assert await pins.list_thread_pin_ids("alice") == ["first", "third", "new", "second"]
    await pins.THREAD_PINS.delete("alice", key="first")
    assert await pins.list_thread_pin_ids("alice") == ["third", "new", "second"]
    assert await pins.list_thread_pin_ids("bob") == ["second", "first"]


async def test_reorder_api_and_tool_cannot_add_pins_or_choose_another_person(
    people: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    await pins.pin_thread("alice", "first")
    await pins.pin_thread("alice", "second")
    await pins.pin_thread("bob", "only-bob")
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[require_session] = lambda: {"sub": "alice"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        for thread_ids in (["only-bob"], ["not-pinned"], ["first", "first"]):
            response = await client.put("/threads/pinned/order", json={"thread_ids": thread_ids})
            assert response.status_code == 400
            assert await pins.list_thread_pin_ids("alice") == ["first", "second"]
        response = await client.put(
            "/threads/pinned/order", json={"thread_ids": ["second", "first"]}
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        app.dependency_overrides.clear()
        response = await client.put("/threads/pinned/order", json={"thread_ids": ["first"]})
        assert response.status_code == 401
    assert await pins.list_thread_pin_ids("alice") == ["second", "first"]
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=None))
    denied = await threads_tool.reorder_thread_pins(["first", "second"])
    assert denied["ok"] is False
    assert (await threads_tool.reorder_sidebar_threads(["first"], ownerless=True))["ok"] is False
    assert await pins.list_thread_pin_ids("alice") == ["second", "first"]
    monkeypatch.setattr(
        threads_tool,
        "_actor",
        AsyncMock(return_value=threads_tool._Actor(login="alice", email=None, name="Alice")),
    )
    assert await threads_tool.reorder_thread_pins(["only-bob"]) == {
        "ok": False,
        "error": "Only your already pinned threads can be reordered",
    }
    assert await threads_tool.reorder_thread_pins(["first"]) == {"ok": True}
    assert await pins.list_thread_pin_ids("alice") == ["first", "second"]
    assert await pins.list_thread_pin_ids("bob") == ["only-bob"]


@pytest.mark.parametrize(
    "folder,hierarchy", [("ownerless", False), ("repo", True), ("project", True)]
)
async def test_folder_order_precedes_pagination_and_keeps_access_and_scope(
    folder: str,
    hierarchy: bool,
    thread_api: ThreadAPI,
    people: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope: dict[str, str | bool] = {"ownerless": True}
    metadata: dict[str, object] = {"participant_logins": {"alice": True, "bob": True}}
    if folder == "project":
        alice = await User.for_login("github", "alice")
        bob = await User.for_login("github", "bob")
        assert alice is not None and bob is not None
        project = await PROJECTS.create(alice, "Shared work")
        await PROJECTS.set_member(project.id, alice, bob, "member")
        scope = {"project": str(project.id)}
        metadata["project_id"] = str(project.id)
    elif folder == "repo":
        scope = {"repo": "Acme/Repo"}
        metadata.update(repo_owner="acme", repo_name="repo")
    for number in range(6):
        thread_id = f"t{number}"
        thread_api.add(thread_id, **metadata)
        thread_api.threads[thread_id]["updated_at"] = f"2026-09-02T00:0{number}:00Z"
    thread_api.add("hidden", **metadata, visibility="private", owner_login="bob")
    thread_api.add(
        "wrong-folder", participant_logins={"alice": True}, repo_owner="acme", repo_name="other"
    )
    monkeypatch.setattr(listing.THREADS, "search", thread_api.indexed_search)
    monkeypatch.setattr(listing, "_THREADS_SEARCH_PAGE", 2)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[require_session] = lambda: {"sub": "alice"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        for thread_ids in (["hidden"], ["wrong-folder"], ["t0", "t0"]):
            response = await client.put("/threads/order", json={**scope, "thread_ids": thread_ids})
            assert response.status_code == 400
        response = await client.put("/threads/order", json={"thread_ids": ["t0"]})
        assert response.status_code == 422
        response = await client.put(
            "/threads/order", json={**scope, "thread_ids": ["t0", "t2", "t1"]}
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        response = await client.put("/threads/order", json={**scope, "thread_ids": ["t2", "t0"]})
        assert response.status_code == 200
        app.dependency_overrides.clear()
        response = await client.put(
            "/threads/order",
            json={**scope, "thread_ids": ["t0"]},
            headers={"Authorization": "Bearer machine-token"},
        )
        assert response.status_code == 401
    page_scope = listing.SidebarOrderScope.model_validate(scope)
    page_kwargs = page_scope.model_dump()
    for offset, expected in enumerate(("t2", "t0", "t1", "t5")):
        page = await listing.list_dashboard_threads_page(
            "alice", hierarchy=hierarchy, offset=offset, limit=1, **page_kwargs
        )
        assert [item["id"] for item in page["items"]] == [expected]
        assert page["hasMore"] is True
    saved = await listing.SIDEBAR_ORDERS.saved("alice", page_scope)
    assert saved.saved_at_ms is not None
    thread_api.add("brand-new", **metadata)
    thread_api.threads["brand-new"]["created_at"] = "2099-01-01T00:00:00Z"
    thread_api.threads["brand-new"]["updated_at"] = "2099-01-01T00:00:00Z"
    for offset, expected in enumerate(("brand-new", "t2", "t0", "t1", "t5")):
        ordered_page = await listing.list_dashboard_threads_page(
            "alice", hierarchy=hierarchy, offset=offset, limit=1, **page_kwargs
        )
        assert [item["id"] for item in ordered_page["items"]] == [expected]
    summary_page = await listing.list_dashboard_threads_page(
        "alice", status="finished", limit=5, **page_kwargs
    )
    assert [item["id"] for item in summary_page["items"]][:4] == ["brand-new", "t2", "t0", "t1"]
    del thread_api.threads["brand-new"]
    bob_page = await listing.list_dashboard_threads_page("bob", limit=1, **page_kwargs)
    assert [item["id"] for item in bob_page["items"]] == ["t5"]
    global_page = await listing.list_dashboard_threads_page("alice", limit=1)
    assert [item["id"] for item in global_page["items"]] == ["t5"]
    cast(dict[str, object], thread_api.threads["t0"]["metadata"]).update(
        visibility="private", owner_login="bob"
    )
    remaining = await listing.list_dashboard_threads_page(
        "alice", hierarchy=hierarchy, limit=3, status="finished", **page_kwargs
    )
    assert [item["id"] for item in remaining["items"]] == ["t2", "t1", "t5"]
    await listing.SIDEBAR_ORDERS.reorder(
        "alice", listing.SidebarThreadOrderBody.model_validate({**scope, "thread_ids": ["t1"]})
    )
    assert await listing.SIDEBAR_ORDERS.ids("alice", page_scope) == ["t1", "t2"]
    if folder == "ownerless":
        assert await listing.SIDEBAR_ORDERS.ids(
            "alice", listing.SidebarOrderScope(project="none")
        ) == ["t1", "t2"]


@pytest.mark.parametrize("hierarchy", [False, True])
async def test_folder_order_keeps_older_unranked_chats_across_pages(
    hierarchy: bool, thread_api: ThreadAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    for number in range(60):
        thread_id = f"t{number:02}"
        thread_api.add(thread_id, title=f"matching {thread_id}", participant_logins={"alice": True})
        thread_api.threads[thread_id]["updated_at"] = f"2026-09-02T00:{number:02}:00Z"
    monkeypatch.setattr(listing.THREADS, "search", thread_api.indexed_search)
    monkeypatch.setattr(listing, "sidebar_memberships", thread_api.sidebar_memberships)
    monkeypatch.setattr(listing.PROJECTS, "filing_for", AsyncMock(return_value=listing.NO_PROJECTS))
    monkeypatch.setattr(
        listing.SIDEBAR_ORDERS,
        "saved",
        AsyncMock(
            return_value=listing.SidebarThreadOrder(
                thread_ids=["t00"], saved_at_ms=1_800_000_000_000
            )
        ),
    )
    cast(dict[str, object], thread_api.threads["t00"]["metadata"])["title"] = "unrelated"
    for query in (None, "matching"):
        expected = (["t00"] if query is None else []) + [
            f"t{number:02}" for number in range(59, 0, -1)
        ]
        for offset in (0, 25, 50):
            page = await listing.list_dashboard_threads_page(
                "alice", ownerless=True, hierarchy=hierarchy, query=query, offset=offset, limit=25
            )
            assert [item["id"] for item in page["items"]] == expected[offset : offset + 25]
            assert page["hasMore"] is (offset + 25 < len(expected))


async def test_folder_order_can_rank_a_readable_parent_projected_from_a_matching_worker(
    thread_api: ThreadAPI, people: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    for number, thread_id in enumerate(("worker", "parent", "recent", "newest")):
        thread_api.add(thread_id, participant_logins={"alice": True})
        thread_api.threads[thread_id]["updated_at"] = f"2026-09-02T00:0{number}:00Z"
    cast(dict[str, object], thread_api.threads["parent"]["metadata"]).update(
        repo_owner="acme", repo_name="other-folder"
    )
    thread_api.memberships = {
        "worker": SidebarTaskMembership("worker", "task", "worker", "parent"),
        "parent": SidebarTaskMembership("parent", "task", "coordinator", "parent"),
    }
    monkeypatch.setattr(listing.THREADS, "search", thread_api.indexed_search)
    monkeypatch.setattr(listing, "sidebar_memberships", thread_api.sidebar_memberships)
    monkeypatch.setattr(listing, "_THREADS_SEARCH_PAGE", 2)
    body = listing.SidebarThreadOrderBody(thread_ids=["parent", "recent"], ownerless=True)
    await listing.SIDEBAR_ORDERS.reorder("alice", body)
    page = await listing.list_dashboard_threads_page(
        "alice", ownerless=True, hierarchy=True, limit=1
    )
    assert [item["id"] for item in page["items"]] == ["parent"]
    assert [item["id"] for item in page["items"][0]["taskWorkers"]] == ["worker"]
    second = await listing.list_dashboard_threads_page(
        "alice", ownerless=True, hierarchy=True, offset=1, limit=1
    )
    assert [item["id"] for item in second["items"]] == ["recent"]
    cast(dict[str, object], thread_api.threads["parent"]["metadata"]).update(
        visibility="private", owner_login="bob"
    )
    with pytest.raises(ValueError, match="Only readable threads"):
        await listing.SIDEBAR_ORDERS.reorder("alice", body)
    filtered = await listing.list_dashboard_threads_page(
        "alice", ownerless=True, hierarchy=True, limit=3
    )
    assert "parent" not in [item["id"] for item in filtered["items"]]
    assert "worker" in [item["id"] for item in filtered["items"]]
