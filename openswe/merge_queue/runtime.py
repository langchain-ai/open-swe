"""Job handlers are inert until the composition root supplies an isolated controller."""

import asyncio
import logging
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

from pydantic import BaseModel, ConfigDict

from openswe.github.http import RepoClient
from openswe.jobs.queue import Job
from openswe.merge_queue.admission import QueuePolicy
from openswe.merge_queue.scheduler import BatchScheduler
from openswe.merge_queue.store import LeaseLost, MergeQueue
from openswe.merge_queue.validation import CandidateValidation

JOB_KIND = "merge_queue_reconcile"
logger = logging.getLogger(__name__)


class QueueJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queue_id: UUID
    workspace_id: UUID


class QueueRuntime:
    def __init__(
        self,
        policies: Mapping[UUID, QueuePolicy],
        open_repo: Callable[[MergeQueue], AbstractAsyncContextManager[RepoClient]],
        *,
        retention_days: int = 90,
    ):
        if not 7 <= retention_days <= 365:
            raise ValueError("Retention must be between 7 and 365 days")
        self.policies, self.open_repo = dict(policies), open_repo
        self.retention_days = retention_days

    async def reconcile(self, payload: QueueJob) -> None:
        policy = self.policies.get(payload.queue_id)
        if policy is None:
            return
        queue = await MergeQueue.load(payload.queue_id, payload.workspace_id)
        if queue is None:
            return
        lease = await queue.claim(uuid7(), timedelta(seconds=60))
        if lease is None:
            return
        try:
            async with asyncio.timeout(240):
                # Expire saved budgets even when GitHub or admission is unavailable.
                await queue.validation_work(policy, lease)
                async with self.open_repo(queue) as repo:
                    await BatchScheduler(queue, repo, policy).reconcile(
                        lease, seconds=180, retention_days=self.retention_days
                    )
                    await queue.renew(lease, timedelta(seconds=60))
                    async with asyncio.timeout(45):
                        await CandidateValidation(queue, repo, policy).reconcile(lease)
        finally:
            try:
                async with asyncio.timeout(10):
                    await queue.prune_history(
                        datetime.now(UTC) - timedelta(days=self.retention_days), lease
                    )
            except LeaseLost:
                logger.info("Queue cleanup lost its lease", extra={"queue_id": str(queue.id)})
            except Exception:
                logger.exception("Queue history cleanup failed", extra={"queue_id": str(queue.id)})
            finally:
                try:
                    async with asyncio.timeout(10):
                        await queue.release(lease)
                except LeaseLost:
                    logger.info("Queue lease no longer held", extra={"queue_id": str(queue.id)})


# Only trusted server composition supplies policy and authentication, never job payloads.
RUNTIME: QueueRuntime | None = None


async def reconcile_queue(job: Job) -> None:
    if RUNTIME is not None:
        await RUNTIME.reconcile(QueueJob.model_validate(job.payload))
