"""Run due jobs on every replica; ``claim_due`` keeps any one job on a single worker."""

import asyncio
import logging
import signal
import threading
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from types import FrameType, TracebackType
from typing import Final, Self

from openswe.jobs.queue import Hold, Job, claim_due

logger = logging.getLogger(__name__)

JobHandler = Callable[[Job], Coroutine[object, object, None]]
SignalHandler = Callable[[int, FrameType | None], object]

_MAX_IN_FLIGHT: Final = 20
_IDLE_SECONDS: Final = 5.0
_BUSY_SECONDS: Final = 0.25
# How often a running job renews its claim and checks it was not cancelled.
_HEARTBEAT_SECONDS: Final = 10.0
_RENEW_TIMEOUT_SECONDS: Final = 5.0
_DRAIN_SECONDS: Final = 30.0


class JobWorker:
    """Claims due jobs and runs each through the handler registered for its kind."""

    def __init__(self, *, max_in_flight: int = _MAX_IN_FLIGHT) -> None:
        self._max_in_flight = max_in_flight
        self._handlers: dict[str, JobHandler] = {}
        self._running: set[asyncio.Task[None]] = set()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._shutdown: asyncio.Task[None] | None = None

    def register(self, kind: str) -> Callable[[JobHandler], JobHandler]:
        def decorate(handler: JobHandler) -> JobHandler:
            if kind in self._handlers:
                raise ValueError(f"A handler for {kind} is already registered")
            self._handlers[kind] = handler
            return handler

        return decorate

    async def run_due(self, limit: int | None = None) -> int:
        """Run what is due now and wait for it; how many jobs ran."""
        jobs = await claim_due(
            self._max_in_flight if limit is None else limit, kinds=list(self._handlers)
        )
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
        work: asyncio.Task[None] | None = None
        try:
            state = await self._renew(job)
            if state in (Hold.CANCELLED, Hold.LOST):
                await self._release(job)
                return
            replaced = state is Hold.REPLACED
            work = asyncio.create_task(handler(job))
            while not (await asyncio.wait({work}, timeout=_HEARTBEAT_SECONDS))[0]:
                state = await self._renew(job)
                if state is Hold.CANCELLED or (state is Hold.LOST and not replaced):
                    await self._stop_handler(work, job)
                    await self._release(job, interrupted=True)
                    logger.warning("Job stopped after losing its lease", extra=extra)
                    return
                replaced = replaced or state is Hold.REPLACED
            try:
                work.result()
            except Exception:
                logger.exception("Job failed", extra=extra)
                await job.retry_later()
                return
            await job.finish()
        except asyncio.CancelledError:
            logger.warning("Job cut short by shutdown", extra=extra)
            if work is not None:
                await self._stop_handler(work, job)
            await self._release(job, interrupted=work is not None)
            raise

    async def _stop_handler(self, work: asyncio.Task[None], job: Job) -> None:
        work.cancel()
        while not (await asyncio.wait({work}, timeout=_HEARTBEAT_SECONDS))[0]:
            await self._renew(job)
        await asyncio.gather(work, return_exceptions=True)

    async def _renew(self, job: Job) -> Hold:
        """Retry transient failures only while the last confirmed lease safely outlasts a retry."""
        try:
            async with asyncio.timeout(_RENEW_TIMEOUT_SECONDS):
                return await job.hold()
        except Exception:
            logger.warning(
                "Could not renew a running job's lease",
                extra={"job_id": str(job.id), "job_kind": job.kind},
                exc_info=True,
            )
            retry_until = datetime.now(UTC) + timedelta(
                seconds=_HEARTBEAT_SECONDS + _RENEW_TIMEOUT_SECONDS
            )
            if job.locked_until is not None and job.locked_until > retry_until:
                return Hold.HELD
            return Hold.LOST

    async def _release(self, job: Job, *, interrupted: bool = False) -> None:
        try:
            await job.release(interrupted=interrupted)
        except Exception:
            logger.warning(
                "Could not release a stopped job's lease",
                extra={"job_id": str(job.id), "job_kind": job.kind},
                exc_info=True,
            )

    async def _loop(self, first: Callable[[], Coroutine[object, object, object]] | None) -> None:
        if first is not None:
            try:
                await first()
            except Exception:
                logger.exception("Job worker startup work failed")
        while not self._stop.is_set():
            started = 0
            try:
                room = self._max_in_flight - len(self._running)
                for job in await claim_due(room, kinds=list(self._handlers)) if room > 0 else []:
                    if self._stop.is_set():
                        await self._release(job)
                        continue
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
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._shutdown = None
        self._task = asyncio.create_task(self._loop(first), name="job-worker")

    def request_stop(self) -> None:
        """Begin draining synchronously when the process receives its shutdown signal."""
        self._stop.set()
        if self._shutdown is None:
            self._shutdown = asyncio.create_task(self._drain(), name="job-worker-shutdown")

    async def _drain(self) -> None:
        if self._task is not None:
            await self._task
        self._task = None
        running = set(self._running)
        if running:
            _, pending = await asyncio.wait(running, timeout=_DRAIN_SECONDS)
            for task in pending:
                task.cancel()
            await asyncio.gather(*running, return_exceptions=True)

    async def stop(self) -> None:
        self.request_stop()
        if self._shutdown is not None:
            await asyncio.shield(self._shutdown)


class WorkerShutdown:
    """Drain Robin's workers on signals without replacing the server's shutdown behavior."""

    def __init__(self, *workers: JobWorker) -> None:
        self._workers = workers
        self._handlers: dict[signal.Signals, SignalHandler] = {}

    def __enter__(self) -> Self:
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGTERM, signal.SIGINT):
                handler = signal.getsignal(sig)
                if callable(handler):
                    self._handlers[sig] = handler
                    signal.signal(sig, self._signal)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        for sig, handler in self._handlers.items():
            if signal.getsignal(sig) == self._signal:
                signal.signal(sig, handler)
        self._handlers.clear()

    def _signal(self, signum: int, frame: FrameType | None) -> None:
        for worker in self._workers:
            worker.request_stop()
        self._handlers[signal.Signals(signum)](signum, frame)


WORKER: Final = JobWorker()
