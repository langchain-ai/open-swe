import pytest

from agent.dashboard import agent_usage


class FakeStore:
    def __init__(self):
        self.values: dict[tuple[tuple[str, ...], str], dict] = {}
        self.search_calls: list[tuple[tuple[str, ...], int]] = []

    async def get_item(self, namespace: list[str], key: str) -> dict | None:
        value = self.values.get((tuple(namespace), key))
        return {"value": value} if value is not None else None

    async def put_item(self, namespace: list[str], key: str, value: dict) -> None:
        self.values[(tuple(namespace), key)] = value

    async def search_items(self, namespace: list[str], *, limit: int, offset: int) -> dict:
        self.search_calls.append((tuple(namespace), offset))
        values = [
            {"value": value}
            for (item_namespace, _), value in self.values.items()
            if item_namespace == tuple(namespace)
        ]
        return {"items": values[offset : offset + limit]}


class FakeThreads:
    def __init__(self, threads: list[dict] | None = None):
        self.threads = threads or []

    async def search(self, *, metadata: dict, limit: int, offset: int) -> list[dict]:
        return self.threads[offset : offset + limit]


class FakeClient:
    def __init__(self, store: FakeStore, threads: list[dict] | None = None):
        self.store = store
        self.threads = FakeThreads(threads)


@pytest.mark.asyncio
async def test_usage_records_runs_and_reads_every_page(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(agent_usage, "_client", lambda: FakeClient(store))
    monkeypatch.setattr(agent_usage, "_PAGE_SIZE", 1)
    monkeypatch.setattr(agent_usage, "_now_ms", lambda: 1_800_000_000_000)
    agent_usage._USAGE_CACHE.clear()

    for run_id in ("run-1", "run-2"):
        await agent_usage.record_agent_run_usage(
            run_id=run_id,
            thread_id="shared-thread",
            github_login="octo",
            user_email="octo@example.com",
            model_id="claude",
            effort=None,
            source="dashboard",
        )
    await agent_usage.record_agent_run_usage(
        run_id="run-1",
        thread_id="shared-thread",
        github_login="octo",
        user_email="octo@example.com",
        model_id="claude",
        effort=None,
        source="dashboard",
    )

    payload = await agent_usage.list_agent_usage_leaderboard(
        period="all", limit=10, current_login="octo", current_email="octo@example.com"
    )

    assert payload["rows"][0]["agent_runs"] == 2
    assert (tuple(agent_usage.AGENT_RUN_NAMESPACE), 1) in store.search_calls


@pytest.mark.asyncio
async def test_reviewer_stats_use_publication_and_resolution_events(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(agent_usage, "_client", lambda: FakeClient(store))
    monkeypatch.setattr(agent_usage, "_now_ms", lambda: 1_800_000_000_000)
    agent_usage._USAGE_CACHE.clear()
    finding = {
        "id": "finding-1",
        "status": "open",
        "severity": "high",
        "category": "correctness",
        "first_seen_sha": "bad",
        "last_confirmed_sha": "bad",
        "github_review_comment_id": 1,
        "interactions": [],
    }

    await agent_usage.record_reviewer_publication(
        thread_id="review-thread",
        owner="langchain-ai",
        repo="open-swe",
        pr_number=1,
        head_sha="bad",
        findings=[finding],
    )
    finding.update(status="resolved", last_confirmed_sha="fixed")
    await agent_usage.record_reviewer_finding_state("review-thread", finding)
    payload = await agent_usage.list_agent_usage_leaderboard(
        period="all", limit=10, current_login=None, current_email=None
    )

    stats = payload["reviewer_stats"]
    assert stats["reviewed_prs"] == 1
    assert stats["surfaced_findings"] == 1
    assert stats["addressed_findings"] == 1
    assert stats["resolved_after_update"] == 1


@pytest.mark.asyncio
async def test_republishing_keeps_the_original_publication_time(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(agent_usage, "_client", lambda: FakeClient(store))
    monkeypatch.setattr(agent_usage, "_now_ms", lambda: 1_000)
    await agent_usage.record_reviewer_publication(
        thread_id="review-thread", owner="o", repo="r", pr_number=1, head_sha="sha", findings=[]
    )
    monkeypatch.setattr(agent_usage, "_now_ms", lambda: 1_800_000_000_000)
    await agent_usage.record_reviewer_publication(
        thread_id="review-thread", owner="o", repo="r", pr_number=1, head_sha="sha", findings=[]
    )

    reviews = await agent_usage._all(agent_usage.REVIEW_NAMESPACE)
    assert [review["published_at_ms"] for review in reviews] == [1_000]


@pytest.mark.asyncio
async def test_current_user_row_survives_the_limit(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(agent_usage, "_client", lambda: FakeClient(store))
    monkeypatch.setattr(agent_usage, "_now_ms", lambda: 1_800_000_000_000)
    agent_usage._USAGE_CACHE.clear()
    for index in range(4):
        await agent_usage.record_agent_run_usage(
            run_id=f"run-{index}",
            thread_id=f"thread-{index}",
            github_login=f"user-{index}",
            user_email=None,
            model_id="claude",
            effort=None,
            source="dashboard",
        )

    kwargs = {"period": "all", "limit": 1, "current_login": "user-3", "current_email": None}
    payload = await agent_usage.list_agent_usage_leaderboard(**kwargs)
    cached = await agent_usage.list_agent_usage_leaderboard(**kwargs)

    for result in (payload, cached):
        assert len(result["rows"]) == 2
        assert result["rows"][-1]["user"]["github_login"] == "user-3"
        assert result["current_user_rank"] == 4


@pytest.mark.asyncio
async def test_legacy_records_are_backfilled_once(monkeypatch):
    store = FakeStore()
    thread = {
        "thread_id": "legacy-review",
        "created_at": "2026-08-01T00:00:00Z",
        "metadata": {
            "kind": "reviewer",
            "pr": {"owner": "o", "name": "r", "number": 3},
            "last_reviewed_sha": "sha",
            "findings": [{"id": "f_1", "status": "open", "github_review_comment_id": 1}],
        },
    }
    monkeypatch.setattr(agent_usage, "_client", lambda: FakeClient(store, [thread]))
    monkeypatch.setattr(agent_usage, "_now_ms", lambda: 1_800_000_000_000)
    agent_usage._USAGE_CACHE.clear()
    store.values[(tuple(agent_usage.LEGACY_THREAD_NAMESPACE), "t-1")] = {
        "thread_id": "t-1",
        "github_login": "octo",
        "model_id": "claude",
        "source": "dashboard",
        "created_at_ms": 1_700_000_000_000,
    }
    store.values[(tuple(agent_usage.LEGACY_PR_NAMESPACE), "o/r#1")] = {
        "owner": "o",
        "repo": "r",
        "pr_number": 1,
        "github_login": "octo",
        "merged": True,
        "additions": 5,
        "deletions": 2,
        "created_at_ms": 1_700_000_000_000,
    }

    payload = await agent_usage.list_agent_usage_leaderboard(
        period="all", limit=10, current_login="octo", current_email=None
    )
    assert payload["rows"][0]["agent_runs"] == 1
    assert payload["rows"][0]["merged_prs"] == 1
    assert payload["reviewer_stats"]["reviewed_prs"] == 1
    assert payload["reviewer_stats"]["surfaced_findings"] == 1

    agent_usage._USAGE_CACHE.clear()
    await agent_usage.list_agent_usage_leaderboard(
        period="all", limit=10, current_login="octo", current_email=None
    )
    assert len(await agent_usage._all(agent_usage.AGENT_RUN_NAMESPACE)) == 1
