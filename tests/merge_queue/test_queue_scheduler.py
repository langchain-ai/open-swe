"""A slow GitHub read must leave time for scheduling and cleanup."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid7

import pytest

from openswe.github.http import RepoClient
from openswe.merge_queue import scheduler
from openswe.merge_queue.admission import QueuePolicy
from openswe.merge_queue.models import Lease, MergeRequest
from openswe.merge_queue.store import MergeQueue, QueueEntry


async def test_stalled_admission_does_not_consume_reconciliation_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scheduler, "ADMISSION_MAX_AGE", timedelta(seconds=0.2), raising=False)
    request = Mock(
        spec=MergeRequest, state="queued", last_checked_at=None, created_at=datetime.now(UTC)
    )
    queue = Mock(spec=MergeQueue, id=uuid7(), target_branch="main")
    queue.repository_name = AsyncMock(return_value="acme/widgets")
    queue.renew = AsyncMock()
    queue.entries = AsyncMock(return_value=[QueueEntry(request, 1)])
    queue.schedule = AsyncMock(return_value=None)
    queue.expired_candidates = AsyncMock(return_value=[])
    repo = Mock(spec=RepoClient, full_name="acme/widgets")
    repo.branch_sha = AsyncMock(return_value="a" * 40)
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def stalled_read() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    repo.pull_request.return_value.queue_state = AsyncMock(side_effect=stalled_read)
    controller = scheduler.BatchScheduler(queue, repo, QueuePolicy(checks={}))
    async with asyncio.timeout(5):
        async with asyncio.TaskGroup() as tasks:
            reconcile = tasks.create_task(controller.reconcile(Lease(uuid7(), 1), seconds=600))
            await started.wait()
            await reconcile
    assert cancelled.is_set()
