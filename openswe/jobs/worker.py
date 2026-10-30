"""Run due jobs on every replica; ``claim_due`` keeps any one job on a single worker."""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Final

from openswe.database import configured
from openswe.jobs.queue import Hold, Job, claim_due

logger = logging.getLogger(__name__)

JobHandler = Callable[[Job], Coroutine[object, object, None]]

_MAX_IN_FLIGHT: Final = 20
_IDLE_SECONDS: Final = 5.0
_BUSY_SECONDS: Final = 0.25
# How often a running job renews its claim and checks it was not cancelled.
_HEARTBEAT_SECONDS: Final = 10.0


class JobWorker:
    """Claims due jobs and runs each through the handler registered for its kind."""

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}
        self._running: set[asyncio.Task[None]] = set()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def register(self, kind: str) -> Callable[[JobHandler], JobHandler]:
        def decorate(handler: JobHandler) -> JobHandler:
            if kind in self._handlers:
                raise ValueError(f"A handler for {kind} is already registered")
            self._handlers[kind] = handler
            return handler

        return decorate

    async def run_due(self, limit: int = _MAX_IN_FLIGHT) -> int:
        """Run what is due now and wait for it; how many jobs ran."""
        jobs = await claim_due(limit)
        await asyncio.gather(*(self._run(job) for job in jobs))
        return len(jobs)

    async def _run(self, job: Job) -> None:
        handler = self._handlers.get(job.kind)
        extra = {"job_id": str(job.id), "job_kind": job.kind}
        if handler is None:
            logger.error("No handler for job kind", extra=extra)
            await job.retry_later()
            return
        if job.lapsed_lease is not None:
            logger.warning(
                "Job taken over after its lease lapsed",
                extra={**extra, "lapsed_lease": job.lapsed_lease.isoformat()},
            )
        work = asyncio.create_task(handler(job))
        try:
            while not (await asyncio.wait({work}, timeout=_HEARTBEAT_SECONDS))[0]:
                if await self._renew(job) is Hold.CANCELLED:
                    work.cancel()
                    await asyncio.gather(work, return_exceptions=True)
                    logger.info("Job cancelled while it ran", extra=extra)
                    return
        except asyncio.CancelledError:
            # Shutdown: the handler stops too, and the job is claimed again once its lease lapses.
            logger.warning("Job cut short by shutdown", extra=extra)
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            raise
        try:
            work.result()
        except Exception:
            logger.exception("Job failed", extra=extra)
            await job.retry_later()
            return
        await job.finish()

    async def _renew(self, job: Job) -> Hold:
        """Renew the job's lease; a failed renewal keeps the lease it has until it lapses."""
        try:
            return await job.hold()
        except Exception:
            logger.warning(
                "Could not renew a running job's lease",
                extra={"job_id": str(job.id), "job_kind": job.kind},
                exc_info=True,
            )
            return Hold.HELD

    async def _loop(self, first: Callable[[], Coroutine[object, object, object]] | None) -> None:
        if first is not None:
            try:
                await first()
            except Exception:
                logger.exception("Job worker startup work failed")
        while not self._stop.is_set():
            started = 0
            try:
                room = _MAX_IN_FLIGHT - len(self._running)
                for job in await claim_due(room) if room > 0 else []:
                    task = asyncio.create_task(self._run(job), name=f"job:{job.kind}")
                    self._running.add(task)
                    task.add_done_callback(self._running.discard)
                    started += 1
            except Exception:
                logger.warning("Job worker iteration failed", exc_info=True)
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=_BUSY_SECONDS if started else _IDLE_SECONDS
                )
            except TimeoutError:
                pass

    def start(
        self, *, first: Callable[[], Coroutine[object, object, object]] | None = None
    ) -> None:
        """Start polling; ``first`` runs once in the background before the first poll."""
        if not configured() or (self._task is not None and not self._task.done()):
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(first), name="job-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
        self._task = None
        # Jobs cut short here are claimed again once their claim lapses.
        for task in self._running:
            task.cancel()
        await asyncio.gather(*self._running, return_exceptions=True)


WORKER: Final = JobWorker()
