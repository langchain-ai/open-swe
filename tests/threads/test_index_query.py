from collections.abc import Sequence

import pytest

from agent.database import postgres
from agent.github.pull_requests import DiffStats, PullRequest
from agent.review.session import REVIEW_CHAT_SOURCE
from agent.threads import listing
from agent.threads.index import mark_thread_index_status, upsert_thread_index_rows
from agent.threads.index_query import decode_thread_cursor
from agent.utils.json_types import JsonObject
from agent.workspaces.store import WORKSPACES, WorkspaceCreate

pytestmark = pytest.mark.usefixtures("registry_db")

BASE_MS = 1_750_000_000_000


@pytest.fixture(autouse=True)
def _index_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THREAD_INDEX_READS", "true")
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")

    def no_langgraph() -> None:
        raise AssertionError("the index read path must not call LangGraph")

    monkeypatch.setattr(listing, "langgraph_client", no_langgraph)


def _thread(
    thread_id: str,
    minute: int,
    *,
    login: str = "octocat",
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
            "participant_logins": {login: True},
            "title": f"Thread {thread_id}",
            "latest_run_id": f"run-{thread_id}",
            "latest_run_status": "success",
            "created_at_ms": BASE_MS + minute * 60_000,
            "updated_at_ms": BASE_MS + minute * 60_000,
            **overrides,
        },
    }


def _automation(thread_id: str, minute: int) -> JsonObject:
    thread = _thread(thread_id, minute)
    metadata = thread["metadata"]
    assert isinstance(metadata, dict)
    del metadata["participant_logins"]
    metadata.update(
        {"source": "schedule", "schedule_id": "nightly", "thread_category": "automation"}
    )
    return thread


async def _seed(*threads: JsonObject) -> None:
    await upsert_thread_index_rows(threads)


def _ids(page: JsonObject) -> list[str]:
    return [item["id"] for item in page["items"]]


async def test_interactive_scope_skips_automations_and_automation_scope_pages_by_cursor() -> None:
    await _seed(
        *(_automation(f"auto-{index:02d}", index) for index in range(40)),
        *(_thread(f"chat-{index}", index * 20 + 5) for index in range(3)),
    )

    interactive = await listing.list_dashboard_threads_page(
        "octocat", scope="interactive", limit=10
    )
    assert _ids(interactive) == ["chat-2", "chat-1", "chat-0"]
    assert interactive["hasMore"] is False
    assert interactive["nextCursor"] is None

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        page = await listing.list_dashboard_threads_page(
            "octocat",
            scope="automation",
            limit=15,
            cursor=decode_thread_cursor(cursor) if cursor else None,
        )
        pages += 1
        seen.extend(_ids(page))
        cursor = page["nextCursor"]
        assert (cursor is not None) is page["hasMore"]
        if cursor is None:
            break
    assert pages == 3
    assert seen == [f"auto-{index:02d}" for index in reversed(range(40))]

    by_offset = await listing.list_dashboard_threads_page(
        "octocat", scope="automation", limit=15, offset=15
    )
    assert _ids(by_offset) == seen[15:30]
    assert by_offset["offset"] == 15


async def test_cursor_continues_through_equal_timestamps() -> None:
    await _seed(*(_thread(f"same-{index}", 1) for index in range(5)))

    first = await listing.list_dashboard_threads_page("octocat", limit=2)
    cursor = decode_thread_cursor(str(first["nextCursor"]))
    second = await listing.list_dashboard_threads_page("octocat", limit=2, cursor=cursor)
    third = await listing.list_dashboard_threads_page(
        "octocat", limit=2, cursor=decode_thread_cursor(str(second["nextCursor"]))
    )

    assert _ids(first) + _ids(second) + _ids(third) == [f"same-{i}" for i in (4, 3, 2, 1, 0)]


async def test_list_filters() -> None:
    await _seed(
        _thread("open", 5, repo_owner="Acme", repo_name="API"),
        _thread("resolved", 4, resolved=True, repo_owner="acme", repo_name="api"),
        _thread("no-repo", 3, last_viewed_run_id="run-no-repo"),
        _thread("other-repo", 2, repo_owner="acme", repo_name="web"),
    )

    assert _ids(await listing.list_dashboard_threads_page("octocat", resolved=False)) == [
        "open",
        "no-repo",
        "other-repo",
    ]
    assert _ids(await listing.list_dashboard_threads_page("octocat", resolved=True)) == ["resolved"]
    assert _ids(await listing.list_dashboard_threads_page("octocat", repo="ACME/api")) == [
        "open",
        "resolved",
    ]
    assert _ids(await listing.list_dashboard_threads_page("octocat", ownerless=True)) == ["no-repo"]
    assert _ids(await listing.list_dashboard_threads_page("octocat", viewed=True)) == ["no-repo"]
    assert _ids(
        await listing.list_dashboard_threads_page("octocat", viewed=False, resolved=False)
    ) == ["open", "other-repo"]


async def test_status_filter_and_summary_follow_the_row_status() -> None:
    await _seed(_thread("busy", 2), _thread("done", 1))
    async with postgres.transaction() as conn:
        await mark_thread_index_status(conn, "busy", status="running", run_id="run-2")

    running = await listing.list_dashboard_threads_page("octocat", status="running")
    finished = await listing.list_dashboard_threads_page("octocat", status="finished")

    assert _ids(running) == ["busy"]
    item = running["items"][0]
    assert item["status"] == "running"
    assert item["viewed"] is False
    assert [item["status"] for item in finished["items"]] == ["finished"]


async def test_private_threads_are_visible_to_owner_and_admin_only() -> None:
    await _seed(
        _thread(
            "secret",
            1,
            login="alice",
            visibility="private",
            participant_logins={"alice": True, "bob": True, "admin": True},
        )
    )

    assert _ids(await listing.list_dashboard_threads_page("alice")) == ["secret"]
    assert _ids(await listing.list_dashboard_threads_page("bob")) == []
    assert _ids(await listing.list_dashboard_threads_page("admin")) == ["secret"]
    assert _ids(await listing.list_dashboard_threads_page("alice", include_private=False)) == []


async def test_review_chat_is_listed_only_for_its_reader() -> None:
    await _seed(
        _thread(
            "review",
            1,
            login="reviewer",
            source=REVIEW_CHAT_SOURCE,
            github_login="Reviewer",
            repo_owner="acme",
            repo_name="api",
            pr_number=7,
            thread_category="review",
        )
    )

    assert _ids(await listing.list_dashboard_threads_page("reviewer")) == ["review"]
    assert _ids(await listing.list_dashboard_threads_page("admin", include_all=True)) == []


async def test_include_all_lists_everyone_for_an_admin() -> None:
    await _seed(
        _thread("alice-thread", 2, login="alice"), _thread("admin-thread", 1, login="admin")
    )

    assert _ids(await listing.list_dashboard_threads_page("admin")) == ["admin-thread"]
    assert _ids(await listing.list_dashboard_threads_page("admin", include_all=True)) == [
        "alice-thread",
        "admin-thread",
    ]


async def test_query_matches_literally() -> None:
    await _seed(
        _thread("percent", 3, title="Raise coverage to 100%"),
        _thread("plain", 2, title="Raise coverage to 1000"),
        _thread("branch", 1, branch_name="fix/under_score"),
    )

    assert _ids(await listing.list_dashboard_threads_page("octocat", query="100%")) == ["percent"]
    assert _ids(await listing.list_dashboard_threads_page("octocat", query="e_c")) == []
    assert _ids(await listing.list_dashboard_threads_page("octocat", query="under_score")) == [
        "branch"
    ]


async def test_repos_group_threads_newest_first() -> None:
    await _seed(
        _thread("old", 1, repo_owner="acme", repo_name="api"),
        _thread("new", 3, repo_owner="Acme", repo_name="API"),
        _thread("web", 2, repo_owner="acme", repo_name="web"),
        _thread("resolved", 9, repo_owner="acme", repo_name="docs", resolved=True),
        _thread("none", 4),
    )
    await WORKSPACES.create(WorkspaceCreate(name="platform", repos=["acme/api"]), "octocat")

    repos = await listing.list_dashboard_thread_repos("octocat")

    assert repos == [
        {
            "repoFullName": "Acme/API",
            "name": "API",
            "updatedAt": BASE_MS + 3 * 60_000,
            "workspace": "platform",
        },
        {
            "repoFullName": "acme/web",
            "name": "web",
            "updatedAt": BASE_MS + 2 * 60_000,
            "workspace": "default",
        },
    ]


async def test_pinned_threads_keep_pin_order_and_drop_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(
        _thread("first", 1),
        _thread("second", 2, unlisted=True),
        _thread("private", 3, login="alice", visibility="private"),
    )

    async def pins(login: str) -> Sequence[str]:
        return ["second", "missing", "private", "first"]

    monkeypatch.setattr(listing, "list_thread_pin_ids", pins)

    pinned = await listing.list_dashboard_pinned_threads("octocat")

    assert [item["id"] for item in pinned] == ["second", "first"]


async def test_page_applies_stored_diff_stats_with_one_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for number in (1, 2):
        await PullRequest(
            owner="acme", repo="api", number=number, additions=number, deletions=0, changed_files=1
        ).save()

    def pr(number: int) -> list[JsonObject]:
        return [
            {
                "repo_full_name": "acme/api",
                "number": number,
                "url": f"https://github.com/acme/api/pull/{number}",
            }
        ]

    await _seed(_thread("one", 1, pull_requests=pr(1)), _thread("two", 2, pull_requests=pr(2)))
    queries = 0
    diff_stats_for = PullRequest.diff_stats_for

    async def counting(prs: Sequence[tuple[str, int]]) -> dict[tuple[str, int], DiffStats]:
        nonlocal queries
        queries += 1
        return await diff_stats_for(prs)

    monkeypatch.setattr(PullRequest, "diff_stats_for", counting)

    page = await listing.list_dashboard_threads_page("octocat")

    assert [item["diffStats"]["additions"] for item in page["items"]] == [2, 1]
    assert queries == 1
