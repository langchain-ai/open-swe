"""Job handlers are inert until the composition root supplies an isolated controller."""

import asyncio
import logging
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid7

from pydantic import BaseModel, ConfigDict

from openswe.github.http import RepoClient
from openswe.jobs.queue import Job
from openswe.merge_queue.admission import QueuePolicy
from openswe.merge_queue.dispatch import DispatchGateway
from openswe.merge_queue.scheduler import BatchScheduler
from openswe.merge_queue.store import LeaseLost, MergeQueue
from openswe.merge_queue.validation import CandidateValidation

if TYPE_CHECKING:
    from openswe.merge_queue.controller import QueueController

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
        dispatcher: DispatchGateway,
        *,
        retention_days: int = 90,
    ):
        if not 7 <= retention_days <= 365:
            raise ValueError("Retention must be between 7 and 365 days")
        self.policies, self.open_repo = dict(policies), open_repo
        self.retention_days = retention_days
        self.dispatcher = dispatcher

    async def reconcile(self, payload: QueueJob) -> None:
        policy = self.policies.get(payload.queue_id)
        if policy is None:
            return
        queue = await MergeQueue.load(payload.queue_id, payload.workspace_id)
        if queue is None or queue.mode != "custom":
            return
        lease = await queue.claim(uuid7(), timedelta(seconds=60))
        if lease is None:
            return
        try:
            async with AsyncExitStack() as resources:
                scheduler = None
                cancelled = False
                try:
                    async with asyncio.timeout(240):
                        # Expire saved budgets even when GitHub or admission is unavailable.
                        await queue.advance_validation(policy, lease)
                        repo = await resources.enter_async_context(self.open_repo(queue))
                        scheduler = BatchScheduler(queue, repo, policy)
                        await scheduler.reconcile(lease, seconds=180)
                        await queue.renew(lease, timedelta(seconds=60))
                        async with asyncio.timeout(45):
                            await CandidateValidation(
                                queue, repo, policy, self.dispatcher
                            ).reconcile(lease)
                except asyncio.CancelledError:
                    cancelled = True
                    raise
                finally:
                    if scheduler is not None and not cancelled:
                        await scheduler.cleanup(lease, retention_days=self.retention_days)
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
RUNTIME: QueueRuntime | QueueController | None = None


async def reconcile_queue(job: Job) -> None:
    payload = QueueJob.model_validate(job.payload)
    if RUNTIME is None:
        from openswe.merge_queue.controller import start_queue

        try:
            await start_queue()
        except ValueError:
            logger.exception("Queue configuration is unavailable")
    if RUNTIME is not None:
        await RUNTIME.reconcile(payload)
        return
    queue = await MergeQueue.load(payload.queue_id, payload.workspace_id)
    if queue is None or queue.mode != "native":
        return
    lease = await queue.claim(uuid7(), timedelta(seconds=60))
    if lease is None:
        return
    try:
        async with asyncio.timeout(10):
            await queue.prune_history(datetime.now(UTC) - timedelta(days=90), lease)
    finally:
        await queue.release(lease)
