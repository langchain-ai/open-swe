from __future__ import annotations

import pytest

from agent.dashboard import agent_usage


class FakeStore:
    def __init__(self, values: dict[tuple[tuple[str, ...], str], dict] | None = None):
        self.values = values or {}
        self.puts: list[tuple[list[str], str, dict]] = []
        self.searches: list[tuple[list[str], int]] = []

    async def get_item(self, namespace: list[str], key: str) -> dict | None:
        value = self.values.get((tuple(namespace), key))
        return {"value": value} if value is not None else None

    async def put_item(self, namespace: list[str], key: str, value: dict) -> None:
        self.puts.append((namespace, key, value))
        self.values[(tuple(namespace), key)] = value

    async def search_items(self, namespace: list[str], limit: int = 1000) -> dict:
        self.searches.append((namespace, limit))
        items = [
            {"value": value}
            for (item_namespace, _), value in self.values.items()
            if item_namespace == tuple(namespace)
        ]
        return {"items": items[:limit]}


class FakeThreads:
    def __init__(self, threads: list[dict]):
        self.threads = threads
        self.calls: list[dict] = []

    async def search(self, **kwargs) -> list[dict]:
        self.calls.append(kwargs)
        offset = kwargs.get("offset") or 0
        limit = kwargs.get("limit") or len(self.threads)
        return self.threads[offset : offset + limit]


class FakeClient:
    def __init__(self, *, store: FakeStore | None = None, threads: FakeThreads | None = None):
        self.store = store or FakeStore()
        self.threads = threads or FakeThreads([])


@pytest.fixture(autouse=True)
def clear_cache_refresh_guard():
    agent_usage._CACHE_REFRESH_IN_FLIGHT.clear()
    yield
    agent_usage._CACHE_REFRESH_IN_FLIGHT.clear()


@pytest.mark.asyncio
async def test_cached_usage_payload_returns_stale_snapshot_and_schedules_refresh(monkeypatch):
    usage_snapshot = {
        "period": "30d",
        "total_members": 2,
        "users": [
            {
                "rank": 1,
                "key": "github:octo",
                "name": "octo",
                "github_login": "octo",
                "email": "octo@example.com",
                "favorite_model": "claude",
                "agent_runs": 3,
                "prs_opened": 2,
                "merged_prs": 1,
                "agent_loc": 10,
                "additions": 8,
                "deletions": 2,
            },
            {
                "rank": 2,
                "key": "email:private@example.com",
                "name": "private",
                "github_login": None,
                "email": "private@example.com",
                "favorite_model": "default",
                "agent_runs": 1,
                "prs_opened": 0,
                "merged_prs": 0,
                "agent_loc": 0,
                "additions": 0,
                "deletions": 0,
            },
        ],
    }
    reviewer_snapshot = {
        "period": "30d",
        "reviewed_prs": 1,
        "prs_with_findings": 1,
        "findings_recorded": 1,
        "surfaced_findings": 1,
        "addressed_findings": 0,
        "resolved_after_update": 0,
        "dismissed_findings": 0,
        "unresolved_surfaced_findings": 1,
        "resolution_rate": 0.0,
        "human_replies": 0,
        "severity_counts": {"medium": 1},
        "top_categories": [{"name": "correctness", "count": 1}],
    }
    store = FakeStore(
        {
            (tuple(agent_usage.USAGE_LEADERBOARD_CACHE_NAMESPACE), "30d"): {
                "generated_at_ms": 1,
                "snapshot": usage_snapshot,
            },
            (tuple(agent_usage.REVIEWER_STATS_CACHE_NAMESPACE), "30d"): {
                "generated_at_ms": 1,
                "snapshot": reviewer_snapshot,
            },
        }
    )
    monkeypatch.setattr(agent_usage, "_client", lambda: FakeClient(store=store))
    monkeypatch.setattr(agent_usage, "_now_ms", lambda: agent_usage._CACHE_TTL_MS + 2)

    usage_refreshes: list[str] = []
    reviewer_refreshes: list[str] = []
    payload = await agent_usage.list_agent_usage_leaderboard(
        period="30d",
        limit=1,
        current_login="octo",
        current_email="octo@example.com",
        schedule_usage_refresh=usage_refreshes.append,
        schedule_reviewer_refresh=reviewer_refreshes.append,
    )

    assert usage_refreshes == ["30d"]
    assert reviewer_refreshes == ["30d"]
    assert payload["rows"][0]["user"]["email"] == "octo@example.com"
    assert payload["total_members"] == 2
    assert payload["reviewer_stats"]["surfaced_findings"] == 1
    assert store.puts == []


@pytest.mark.asyncio
async def test_cache_miss_schedules_refresh_without_blocking(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(agent_usage, "_client", lambda: FakeClient(store=store))

    async def fail_refresh(period):
        raise AssertionError(f"refresh should have been scheduled, not awaited: {period}")

    monkeypatch.setattr(agent_usage, "refresh_usage_leaderboard_cache", fail_refresh)
    monkeypatch.setattr(agent_usage, "refresh_reviewer_stats_cache", fail_refresh)
    usage_refreshes: list[str] = []
    reviewer_refreshes: list[str] = []

    payload = await agent_usage.list_agent_usage_leaderboard(
        period="7d",
        limit=10,
        current_login="octo",
        current_email="octo@example.com",
        schedule_usage_refresh=usage_refreshes.append,
        schedule_reviewer_refresh=reviewer_refreshes.append,
    )

    duplicate_payload = await agent_usage.list_agent_usage_leaderboard(
        period="7d",
        limit=10,
        current_login="octo",
        current_email="octo@example.com",
        schedule_usage_refresh=usage_refreshes.append,
        schedule_reviewer_refresh=reviewer_refreshes.append,
    )

    assert usage_refreshes == ["7d"]
    assert reviewer_refreshes == ["7d"]
    assert payload["period"] == "7d"
    assert payload["rows"] == []
    assert payload["generated_at_ms"] is None
    assert payload["reviewer_stats"]["generated_at_ms"] is None
    assert duplicate_payload["rows"] == []
    assert store.searches == []


@pytest.mark.asyncio
async def test_precompute_usage_caches_refreshes_stale_snapshots(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(agent_usage, "_client", lambda: FakeClient(store=store))
    usage_refreshes: list[str | None] = []
    reviewer_refreshes: list[str | None] = []

    async def refresh_usage(period):
        usage_refreshes.append(period)
        snapshot = agent_usage._empty_usage_snapshot(agent_usage._normalize_period(period))
        await store.put_item(
            agent_usage.USAGE_LEADERBOARD_CACHE_NAMESPACE,
            agent_usage._normalize_period(period),
            {"generated_at_ms": agent_usage._now_ms(), "snapshot": snapshot},
        )
        return snapshot

    async def refresh_reviewer(period):
        reviewer_refreshes.append(period)
        snapshot = agent_usage._empty_reviewer_stats_snapshot(agent_usage._normalize_period(period))
        await store.put_item(
            agent_usage.REVIEWER_STATS_CACHE_NAMESPACE,
            agent_usage._normalize_period(period),
            {"generated_at_ms": agent_usage._now_ms(), "snapshot": snapshot},
        )
        return snapshot

    monkeypatch.setattr(agent_usage, "refresh_usage_leaderboard_cache", refresh_usage)
    monkeypatch.setattr(agent_usage, "refresh_reviewer_stats_cache", refresh_reviewer)

    refreshed = await agent_usage.precompute_usage_caches(("30d",))

    assert refreshed == {"usage": 1, "reviewer": 1}
    assert usage_refreshes == ["30d"]
    assert reviewer_refreshes == ["30d"]
    assert agent_usage._CACHE_REFRESH_IN_FLIGHT == set()


@pytest.mark.asyncio
async def test_reviewer_stats_snapshot_counts_surfaced_and_resolved_findings(monkeypatch):
    threads = [
        {
            "created_at": "2025-01-01T00:00:00Z",
            "metadata": {
                "kind": "reviewer",
                "head_sha": "fixed-sha",
                "pr": {"owner": "langchain-ai", "name": "open-swe", "number": 1},
                "findings": [
                    {
                        "id": "f_1",
                        "status": "resolved",
                        "severity": "high",
                        "category": "correctness",
                        "first_seen_sha": "buggy-sha",
                        "github_thread_resolved": True,
                        "github_review_comment_id": 10,
                        "resolution_note": "Fixed in a follow-up commit.",
                        "interactions": [{"kind": "human_reply"}],
                    },
                    {
                        "id": "f_2",
                        "status": "open",
                        "severity": "medium",
                        "category": "performance",
                        "github_review_comment_id": 11,
                    },
                    {
                        "id": "f_3",
                        "status": "dismissed",
                        "severity": "low",
                        "category": "style",
                        "github_review_id": 12,
                    },
                ],
            },
        }
    ]
    monkeypatch.setattr(agent_usage, "_client", lambda: FakeClient(threads=FakeThreads(threads)))

    snapshot = await agent_usage._build_reviewer_stats_snapshot("all")

    assert snapshot["reviewed_prs"] == 1
    assert snapshot["prs_with_findings"] == 1
    assert snapshot["findings_recorded"] == 3
    assert snapshot["surfaced_findings"] == 3
    assert snapshot["addressed_findings"] == 1
    assert snapshot["resolved_after_update"] == 1
    assert snapshot["dismissed_findings"] == 1
    assert snapshot["unresolved_surfaced_findings"] == 1
    assert snapshot["human_replies"] == 1
    assert snapshot["severity_counts"] == {"high": 1, "medium": 1, "low": 1}
    assert snapshot["top_categories"] == [
        {"name": "correctness", "count": 1},
        {"name": "performance", "count": 1},
        {"name": "style", "count": 1},
    ]


@pytest.mark.asyncio
async def test_reviewer_stats_paginates_reviewer_threads(monkeypatch):
    threads = [
        {"created_at": "2025-01-03T00:00:00Z", "metadata": {"kind": "reviewer", "findings": []}},
        {"created_at": "2025-01-02T00:00:00Z", "metadata": {"kind": "reviewer", "findings": []}},
        {"created_at": "2025-01-01T00:00:00Z", "metadata": {"kind": "reviewer", "findings": []}},
    ]
    fake_threads = FakeThreads(threads)
    monkeypatch.setattr(agent_usage, "_CACHE_SEARCH_LIMIT", 2)
    monkeypatch.setattr(agent_usage, "_client", lambda: FakeClient(threads=fake_threads))

    snapshot = await agent_usage._build_reviewer_stats_snapshot("all")

    assert snapshot["reviewed_prs"] == 3
    assert [call["offset"] for call in fake_threads.calls] == [0, 2]
    assert all(call["sort_by"] == "created_at" for call in fake_threads.calls)


@pytest.mark.asyncio
async def test_reviewer_stats_stops_after_page_older_than_cutoff(monkeypatch):
    threads = [
        {"created_at": "2025-01-03T00:00:00Z", "metadata": {"kind": "reviewer", "findings": []}},
        {"created_at": "2025-01-01T00:00:00Z", "metadata": {"kind": "reviewer", "findings": []}},
        {"created_at": "2024-12-31T00:00:00Z", "metadata": {"kind": "reviewer", "findings": []}},
    ]
    fake_threads = FakeThreads(threads)
    monkeypatch.setattr(agent_usage, "_CACHE_SEARCH_LIMIT", 2)
    monkeypatch.setattr(agent_usage, "_client", lambda: FakeClient(threads=fake_threads))
    cutoff_ms = agent_usage._timestamp_ms("2025-01-02T00:00:00Z")

    pages = [page async for page in agent_usage._iter_reviewer_thread_pages(cutoff_ms)]

    assert len(pages) == 1
    assert [call["offset"] for call in fake_threads.calls] == [0]
