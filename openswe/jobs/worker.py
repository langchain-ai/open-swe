"""Run due jobs on every replica; ``claim_due`` keeps any one job on a single worker."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Final

from openswe.database import configured
from openswe.jobs.queue import Job, claim_due

logger = logging.getLogger(__name__)

JobHandler = Callable[[Job], Awaitable[None]]

_BATCH: Final = 20
_IDLE_SECONDS: Final = 5.0
_BUSY_SECONDS: Final = 0.25


class JobWorker:
    """Claims due jobs and runs each through the handler registered for its kind."""

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def register(self, kind: str) -> Callable[[JobHandler], JobHandler]:
        def decorate(handler: JobHandler) -> JobHandler:
            if kind in self._handlers:
                raise ValueError(f"A handler for {kind} is already registered")
            self._handlers[kind] = handler
            return handler

        return decorate

    async def run_due(self, limit: int = _BATCH) -> int:
        """Run what is due now; how many jobs ran."""
        jobs = await claim_due(limit)
        await asyncio.gather(*(self._run(job) for job in jobs))
        return len(jobs)

    async def _run(self, job: Job) -> None:
        handler = self._handlers.get(job.kind)
        if handler is None:
            logger.error(
                "No handler for job kind", extra={"job_id": str(job.id), "job_kind": job.kind}
            )
            await job.retry_later()
            return
        try:
            await handler(job)
        except Exception:
            logger.exception("Job failed", extra={"job_id": str(job.id), "job_kind": job.kind})
            await job.retry_later()
            return
        await job.finish()

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                ran = await self.run_due()
            except Exception:
                ran = 0
                logger.warning("Job worker iteration failed", exc_info=True)
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=_BUSY_SECONDS if ran else _IDLE_SECONDS
                )
            except TimeoutError:
                pass

    def start(self) -> None:
        if not configured() or (self._task is not None and not self._task.done()):
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="job-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
        self._task = None


WORKER: Final = JobWorker()
