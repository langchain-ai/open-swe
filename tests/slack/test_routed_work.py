import asyncio
from types import SimpleNamespace

import pytest

from openswe.slack import routed_work
from openswe.slack.routed_work import RoutedWork
from openswe.tasks.store import SidebarTaskMembership

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.mark.parametrize(
    ("running", "unreadable", "waiting"),
    [
        (set(), set(), False),
        ({"worker-2"}, set(), True),
        ({"worker-2"}, {"worker-3"}, True),
    ],
)
async def test_delegated_work_reads_every_worker_run_at_once(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    running: set[str],
    unreadable: set[str],
    waiting: bool,
) -> None:
    workers = {
        thread_id: SidebarTaskMembership(thread_id, "task", "worker", "coordinator")
        for thread_id in ("worker-1", "worker-2", "worker-3")
    }
    all_reading = asyncio.Barrier(len(workers))

    async def memberships(
        thread_ids: list[str], *, workers_of: bool = False
    ) -> dict[str, SidebarTaskMembership]:
        return workers if workers_of and thread_ids == ["coordinator"] else {}

    async def indexed(thread_id: str) -> SimpleNamespace:
        return SimpleNamespace(thread_id=thread_id, runtime="mda", metadata={})

    async def latest_run(thread_id: str) -> SimpleNamespace:
        await all_reading.wait()
        if thread_id in unreadable:
            raise RuntimeError("MDA is unavailable")
        return SimpleNamespace(run_id=f"{thread_id}-run", active=thread_id in running)

    monkeypatch.setattr(routed_work, "sidebar_memberships", memberships)
    monkeypatch.setattr(routed_work.THREADS, "get", indexed)
    monkeypatch.setattr(
        type(routed_work.RUNTIMES),
        "runs_on",
        lambda _self, _runtime: SimpleNamespace(latest_run=latest_run),
    )

    async with asyncio.timeout(2):
        assert await RoutedWork("coordinator").delegated_active(prune=False) is waiting
    assert ("Could not check a delegated worker" in caplog.text) == bool(unreadable)
