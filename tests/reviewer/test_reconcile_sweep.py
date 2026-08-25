import pytest

from agent import reconcile
from agent.dashboard.thread_registry import (
    SqliteRegistry,
    ThreadCreate,
    set_thread_registry_for_testing,
)


class _Runs:
    def __init__(self, statuses=None) -> None:
        self.statuses = statuses or {}

    async def get(self, thread_id: str, run_id: str):
        value = self.statuses.get(thread_id, "pending")
        if isinstance(value, Exception):
            raise value
        return {"run_id": run_id, "status": value}


class _Client:
    def __init__(self, statuses=None) -> None:
        self.runs = _Runs(statuses)


@pytest.fixture
async def registry(tmp_path):
    value = SqliteRegistry(tmp_path / "reconcile.sqlite3")
    await value.initialize()
    set_thread_registry_for_testing(value)
    try:
        yield value
    finally:
        set_thread_registry_for_testing(None)
        await value.close()


async def test_corrects_stale_cloud_run_from_authoritative_run_status(
    monkeypatch: pytest.MonkeyPatch, registry
) -> None:
    await registry.create(ThreadCreate(id="cloud", owner_login="owner"))
    await registry.transition("cloud", "run", "queued", environment="cloud")
    monkeypatch.setattr(reconcile, "langgraph_client", lambda: _Client({"cloud": "success"}))

    counts = await reconcile.reconcile_stale_runs(max_age_seconds=-1)

    assert counts == {
        "threads_checked": 1,
        "stale_runs": 1,
        "corrected": 1,
        "events_pruned": 0,
    }
    assert (await registry.get("cloud")).status == "finished"


async def test_marks_stale_local_run_error_when_device_is_offline(
    monkeypatch: pytest.MonkeyPatch, registry
) -> None:
    await registry.create(
        ThreadCreate(
            id="local",
            owner_login="owner",
            environment="local",
            device_id="device",
        )
    )
    await registry.transition(
        "local",
        "run",
        "queued",
        environment="local",
        device_id="device",
    )
    monkeypatch.setattr(reconcile, "langgraph_client", lambda: _Client())

    counts = await reconcile.reconcile_stale_runs(max_age_seconds=-1)

    assert counts == {
        "threads_checked": 1,
        "stale_runs": 1,
        "corrected": 1,
        "events_pruned": 0,
    }
    assert (await registry.get("local")).status == "error"


async def test_one_failed_lookup_does_not_abort_the_sweep(
    monkeypatch: pytest.MonkeyPatch, registry
) -> None:
    for thread_id in ("bad", "good"):
        await registry.create(ThreadCreate(id=thread_id, owner_login="owner"))
        await registry.transition(thread_id, f"run-{thread_id}", "queued", environment="cloud")
    monkeypatch.setattr(
        reconcile,
        "langgraph_client",
        lambda: _Client({"bad": RuntimeError("unavailable"), "good": "success"}),
    )

    counts = await reconcile.reconcile_stale_runs(max_age_seconds=-1)

    assert counts == {
        "threads_checked": 2,
        "stale_runs": 2,
        "corrected": 1,
        "events_pruned": 0,
    }
    assert (await registry.get("good")).status == "finished"


async def test_paginates_registry_rows(monkeypatch: pytest.MonkeyPatch, registry) -> None:
    for index in range(reconcile._PAGE_SIZE + 1):
        thread_id = f"thread-{index:03d}"
        await registry.create(ThreadCreate(id=thread_id, owner_login="owner"))
        await registry.transition(thread_id, f"run-{index}", "queued", environment="cloud")
    monkeypatch.setattr(reconcile, "langgraph_client", lambda: _Client())

    counts = await reconcile.reconcile_stale_runs(max_age_seconds=-1)

    assert counts == {
        "threads_checked": reconcile._PAGE_SIZE + 1,
        "stale_runs": reconcile._PAGE_SIZE + 1,
        "corrected": 0,
        "events_pruned": 0,
    }
