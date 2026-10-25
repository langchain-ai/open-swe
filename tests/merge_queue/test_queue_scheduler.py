"""Bound reconciliation time and sandbox use while preserving cleanup progress."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid7

import pytest

from openswe.github.http import RepoClient
from openswe.merge_queue import scheduler
from openswe.merge_queue.admission import QueuePolicy
from openswe.merge_queue.git import Candidate, CandidateGit, PullHead
from openswe.merge_queue.models import Batch, Lease, MergeRequest
from openswe.merge_queue.store import MergeQueue, QueueEntry


async def test_stalled_admission_does_not_consume_reconciliation_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scheduler, "ADMISSION_MAX_AGE", timedelta(seconds=0.2), raising=False)
    request = Mock(
        spec=MergeRequest, state="queued", last_checked_at=None, created_at=datetime.now(UTC)
    )
    queue = Mock(spec=MergeQueue, id=uuid7(), target_branch="main", mode="custom")
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


@pytest.mark.parametrize("scenario", ["replaced_ref", "stalled", "slow_provisioning"])
async def test_cleanup_reuses_one_sandbox_and_preserves_unfinished_refs(scenario: str) -> None:
    candidate = Candidate(
        repository="https://github.com/acme/widgets.git",
        branch="main",
        base_sha="a" * 40,
        pulls=(PullHead(number=1, sha="b" * 40),),
        merges=("c" * 40,),
        tree_sha="d" * 40,
    )
    batches = [
        Mock(spec=Batch, id=uuid7(), candidate=candidate.model_dump(mode="json")) for _ in range(3)
    ]
    queue = Mock(spec=MergeQueue, id=uuid7(), target_branch="main", mode="custom")
    queue.repository_name = AsyncMock(return_value="acme/widgets")
    queue.renew = AsyncMock()
    queue.entries = AsyncMock(return_value=[])
    queue.schedule = AsyncMock(return_value=None)
    queue.expired_candidates = AsyncMock(return_value=batches)
    queue.candidate_deleted = AsyncMock()
    repo = Mock(spec=RepoClient, full_name="acme/widgets")
    repo.branch_sha = AsyncMock(return_value="a" * 40)
    git = Mock(spec=CandidateGit)

    async def delete(batch_id: UUID, sha: str) -> None:
        if batch_id == batches[1].id and scenario != "slow_provisioning":
            if scenario == "stalled":
                await asyncio.Event().wait()
            raise ValueError("Candidate ref changed; refusing deletion")

    git.delete = AsyncMock(side_effect=delete)
    sandboxes = 0

    @asynccontextmanager
    async def candidate_git() -> AsyncIterator[CandidateGit]:
        nonlocal sandboxes
        sandboxes += 1
        if scenario == "slow_provisioning":
            await asyncio.sleep(0.2)
        yield git

    repo.candidate_git = candidate_git
    await scheduler.BatchScheduler(queue, repo, QueuePolicy(checks={})).cleanup(
        Lease(uuid7(), 1), seconds=0.1
    )
    assert sandboxes == 1
    deleted = {call.args[0] for call in queue.candidate_deleted.await_args_list}
    expected = {batch.id for batch in batches}
    if scenario == "stalled":
        expected = {batches[0].id}
    elif scenario == "replaced_ref":
        expected.remove(batches[1].id)
    assert deleted == expected
