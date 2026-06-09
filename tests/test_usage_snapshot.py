from __future__ import annotations

import inspect

import pytest

from agent import usage_snapshot
from agent.dashboard import usage_snapshot_cron


class FakeStore:
    def __init__(self) -> None:
        self.values: dict[tuple[tuple[str, ...], str], dict] = {}

    async def get_item(self, namespace, key):
        value = self.values.get((tuple(namespace), key))
        return {"value": value} if value is not None else None

    async def put_item(self, namespace, key, value):
        self.values[(tuple(namespace), key)] = value


class FakeCrons:
    def __init__(self, existing: list[dict] | None = None) -> None:
        self.existing = existing or []
        self.created: list[dict] = []
        self.deleted: list[str] = []

    async def search(self, **kwargs):
        return self.existing

    async def create(self, assistant_id, **kwargs):
        self.created.append({"assistant_id": assistant_id, **kwargs})
        return {"cron_id": "cron-new"}

    async def delete(self, cron_id):
        self.deleted.append(cron_id)


@pytest.fixture(autouse=True)
def _reset_cron_memo():
    usage_snapshot_cron._registered_cron_id = None
    yield
    usage_snapshot_cron._registered_cron_id = None


class FakeRuns:
    def __init__(self) -> None:
        self.created: list[tuple] = []

    async def create(self, thread, assistant_id, **kwargs):
        self.created.append((thread, assistant_id))
        return {"run_id": "run-1"}


class FakeClient:
    def __init__(self, store=None, crons=None, runs=None):
        self.store = store or FakeStore()
        self.crons = crons or FakeCrons()
        self.runs = runs or FakeRuns()


@pytest.mark.asyncio
async def test_builder_refreshes_all_periods(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def fake_usage(period):
        calls.append(("usage", period))

    async def fake_reviewer(period):
        calls.append(("reviewer", period))

    monkeypatch.setattr(usage_snapshot, "refresh_usage_leaderboard_cache", fake_usage)
    monkeypatch.setattr(usage_snapshot, "refresh_reviewer_stats_cache", fake_reviewer)
    monkeypatch.setenv("USAGE_SNAPSHOT_CRON_ENABLED", "true")

    result = await usage_snapshot._build({}, {})

    assert result["result"]["status"] == "ok"
    assert ("usage", "7d") in calls
    assert ("reviewer", "all") in calls
    assert len(calls) == 6


@pytest.mark.asyncio
async def test_builder_kill_switch(monkeypatch):
    monkeypatch.setenv("USAGE_SNAPSHOT_CRON_ENABLED", "false")
    result = await usage_snapshot._build({}, {})
    assert result["result"]["status"] == "disabled"


@pytest.mark.asyncio
async def test_builder_partial_on_failure(monkeypatch):
    async def fake_usage(period):
        raise RuntimeError("boom")

    async def fake_reviewer(period):
        return None

    monkeypatch.setattr(usage_snapshot, "refresh_usage_leaderboard_cache", fake_usage)
    monkeypatch.setattr(usage_snapshot, "refresh_reviewer_stats_cache", fake_reviewer)
    monkeypatch.setenv("USAGE_SNAPSHOT_CRON_ENABLED", "true")

    result = await usage_snapshot._build({}, {})
    assert result["result"]["status"] == "partial"
    assert len(result["result"]["failed"]) == 3


@pytest.mark.asyncio
async def test_cron_registration_idempotent_and_creates_once(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(usage_snapshot_cron, "_client", lambda: client)

    first = await usage_snapshot_cron.ensure_usage_snapshot_cron()
    assert first == "cron-new"
    assert len(client.crons.created) == 1

    # Second call reads the persisted id, creates nothing new.
    second = await usage_snapshot_cron.ensure_usage_snapshot_cron()
    assert second == "cron-new"
    assert len(client.crons.created) == 1


@pytest.mark.asyncio
async def test_cron_reuses_existing_search_hit(monkeypatch):
    client = FakeClient(crons=FakeCrons(existing=[{"cron_id": "cron-existing"}]))
    monkeypatch.setattr(usage_snapshot_cron, "_client", lambda: client)

    cron_id = await usage_snapshot_cron.ensure_usage_snapshot_cron()
    assert cron_id == "cron-existing"
    assert client.crons.created == []


@pytest.mark.asyncio
async def test_cron_reaps_duplicate_search_hits(monkeypatch):
    client = FakeClient(
        crons=FakeCrons(existing=[{"cron_id": "cron-a"}, {"cron_id": "cron-b"}]),
    )
    monkeypatch.setattr(usage_snapshot_cron, "_client", lambda: client)

    cron_id = await usage_snapshot_cron.ensure_usage_snapshot_cron()
    assert cron_id == "cron-a"
    assert client.crons.deleted == ["cron-b"]
    assert client.crons.created == []


@pytest.mark.asyncio
async def test_cron_memoizes_and_skips_loopback(monkeypatch):
    store = FakeStore()
    client = FakeClient(store=store)
    monkeypatch.setattr(usage_snapshot_cron, "_client", lambda: client)

    await usage_snapshot_cron.ensure_usage_snapshot_cron()

    # After registration, a poisoned client proves no further loopback happens.
    def _boom():
        raise AssertionError("ensure_usage_snapshot_cron should be memoized")

    monkeypatch.setattr(usage_snapshot_cron, "_client", _boom)
    assert await usage_snapshot_cron.ensure_usage_snapshot_cron() == "cron-new"


@pytest.mark.asyncio
async def test_trigger_build_schedules_threadless_run(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(usage_snapshot_cron, "_client", lambda: client)

    result = await usage_snapshot_cron.trigger_usage_snapshot_build()
    assert result["status"] == "scheduled"
    assert client.runs.created == [(None, "usage_snapshot")]


def test_lifespan_retains_no_background_task():
    """Regression guard for the #1434 bug: lifespan must not spawn a looping task."""
    from agent import webapp

    src = inspect.getsource(webapp.lifespan)
    assert "create_task" not in src
    assert "while True" not in src
