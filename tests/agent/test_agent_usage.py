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


class FakeClient:
    def __init__(self, store: FakeStore):
        self.store = store


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
