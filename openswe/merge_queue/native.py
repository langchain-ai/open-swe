"""Observe GitHub's queue without constructing, validating, or merging candidates."""

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from uuid import UUID, uuid7

from openswe.github.http import RepoClient
from openswe.merge_queue.runtime import QueueJob
from openswe.merge_queue.store import LeaseLost, MergeQueue

logger = logging.getLogger(__name__)


class NativeQueueRuntime:
    def __init__(
        self,
        queue_id: UUID,
        repository_id: int,
        open_repo: Callable[[MergeQueue], AbstractAsyncContextManager[RepoClient]],
    ):
        self.queue_id, self.repository_id, self.open_repo = queue_id, repository_id, open_repo

    async def reconcile(self, payload: QueueJob) -> None:
        if payload.queue_id != self.queue_id:
            return
        queue = await MergeQueue.load(payload.queue_id, payload.workspace_id)
        if queue is None or queue.mode != "native":
            return
        lease = await queue.claim(uuid7(), timedelta(seconds=60))
        if lease is None:
            return
        try:
            async with asyncio.timeout(45), self.open_repo(queue) as repo:
                snapshot = await repo.native_queue(queue.target_branch)
                if snapshot.repository_id != self.repository_id:
                    raise ValueError("Native queue repository identity changed")
                await queue.record_native_snapshot(snapshot, lease)
        finally:
            try:
                async with asyncio.timeout(10):
                    await queue.release(lease)
            except LeaseLost:
                logger.info("Native queue lease no longer held", extra={"queue_id": str(queue.id)})
