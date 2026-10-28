import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openswe.review_scout import launch

pytestmark = pytest.mark.usefixtures("no_local_threads")


@pytest.mark.parametrize("active", [None, "active-run"])
async def test_scout_records_security_identity_before_starting_or_reusing_a_run(
    monkeypatch: pytest.MonkeyPatch, active: str | None
) -> None:
    stored: dict[str, object] = {"title": "Walkthrough", "title_locked": True}

    async def update(*, thread_id: str, metadata: dict[str, object]) -> None:
        stored.update(metadata)

    threads = SimpleNamespace(
        create=AsyncMock(return_value={"metadata": stored}),
        update=AsyncMock(side_effect=update),
    )
    client = SimpleNamespace(threads=threads)
    monkeypatch.setattr(launch, "dispatch_client", lambda: client)
    target = launch.ReviewScoutTarget(
        owner="acme",
        repo="api",
        pr_number=7,
        pr_title="Change",
        base_sha="base",
        head_sha="head",
        workspace_slug="workspace",
    )

    async def active_run(self: launch.ReviewScoutTarget) -> str | None:
        assert stored == {
            "title": "Walkthrough",
            "title_locked": True,
            "kind": "review-scout",
            "pr": {"owner": "acme", "name": "api", "number": 7},
        }
        return active

    monkeypatch.setattr(launch.ReviewScoutTarget, "active_run", active_run)
    monkeypatch.setattr(launch, "create_durable_run", AsyncMock(return_value={"run_id": "new-run"}))

    assert await target.start() == (active or "new-run")


@pytest.mark.parametrize("stage", ["lookup", "startup", "poll"])
async def test_walkthrough_wait_bounds_lookup_startup_and_polling(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    cancelled = asyncio.Event()

    async def wait() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def walkthrough(self: launch.ReviewScoutTarget) -> None:
        if stage == "lookup":
            await wait()

    async def start(self: launch.ReviewScoutTarget) -> str:
        if stage == "startup":
            await wait()
        return "run-1"

    async def current_thread_id(self: launch.ReviewScoutTarget) -> str:
        return "scout-thread"

    async def poll(thread_id: str, run_id: str) -> None:
        await wait()

    monkeypatch.setattr(launch.postgres, "configured", lambda: True)
    monkeypatch.setattr(launch, "SCOUT_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(launch.ReviewScoutTarget, "walkthrough", walkthrough)
    monkeypatch.setattr(launch.ReviewScoutTarget, "start", start)
    monkeypatch.setattr(launch.ReviewScoutTarget, "current_thread_id", current_thread_id)
    monkeypatch.setattr(
        launch, "dispatch_client", lambda: SimpleNamespace(runs=SimpleNamespace(get=poll))
    )
    target = launch.ReviewScoutTarget(
        owner="acme",
        repo="widgets",
        pr_number=7,
        pr_title="Change",
        base_sha="base",
        head_sha="head",
        workspace_slug=None,
    )
    async with asyncio.timeout(1):
        assert await target.await_walkthrough() is None
    assert cancelled.is_set()
