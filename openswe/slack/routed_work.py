import asyncio
import logging
from collections.abc import Mapping, Sequence

from openswe.message_queue import QueuedMessage
from openswe.tasks.messages import TaskMessage
from openswe.tasks.store import TaskMembership, sidebar_memberships
from openswe.threads.runtimes import RUNTIMES
from openswe.threads.store import THREADS
from openswe.utils.background_task_state import RUNNING_BACKGROUND_TASKS_KEY
from openswe.utils.thread_pr_state import agent_thread_pr_state_lock

logger = logging.getLogger(__name__)

DESTINATIONS_KEY = "routed_work_destinations"
ORIGINS_KEY = "routed_work_origins"
ANSWERED_RUN_KEY = "routed_work_answered_run"
_WORKER_READS = 8


class RoutedWork:
    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id

    @staticmethod
    def _thread_ids(value: object) -> list[str]:
        return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []

    async def merge(
        self,
        key: str,
        *,
        add: Sequence[str] = (),
        remove: Sequence[str] = (),
    ) -> None:
        async with agent_thread_pr_state_lock(self.thread_id):
            thread = await THREADS.get(self.thread_id)
            if thread is None:
                logger.warning("Routed thread is missing", extra={"thread_id": self.thread_id})
                return
            current = set(self._thread_ids(thread.metadata.get(key)))
            updated = sorted((current | set(add)) - set(remove))
            if updated != sorted(current):
                await THREADS.update(self.thread_id, metadata={key: list(updated)})

    async def track(self, destination: str) -> None:
        if self.thread_id == destination:
            return
        await RoutedWork(destination).merge(ORIGINS_KEY, add=[self.thread_id])
        await self.merge(DESTINATIONS_KEY, add=[destination])

    async def answered(self, run_id: str) -> None:
        try:
            await THREADS.update(self.thread_id, metadata={ANSWERED_RUN_KEY: run_id})
        except Exception:
            logger.exception(
                "Could not record routed work answer",
                extra={"thread_id": self.thread_id, "run_id": run_id},
            )

    async def active(self) -> bool:
        thread = await THREADS.get(self.thread_id)
        if thread is None:
            return False
        run = await RUNTIMES.runs_on(thread.runtime).latest_run(self.thread_id)
        if run is not None and run.active:
            return True
        answered = run is not None and thread.metadata.get(ANSWERED_RUN_KEY) == run.run_id
        if thread.metadata.get(RUNNING_BACKGROUND_TASKS_KEY) and not answered:
            return True
        if await TaskMessage.pending_threads([self.thread_id]):
            return True
        return bool(await QueuedMessage.for_thread(self.thread_id))

    async def delegated_active(
        self,
        *,
        visited: frozenset[str] = frozenset(),
        prune: bool = True,
        metadata: Mapping[str, object] | None = None,
        reads: asyncio.Semaphore | None = None,
    ) -> bool:
        """Whether work this thread handed off is still going.

        ``reads`` bounds how many of its workers are checked at once. A caller
        checking several threads passes one, so the bound covers them all.
        """
        if self.thread_id in visited:
            return False
        visited = visited | {self.thread_id}
        if await TaskMessage.pending_threads([self.thread_id]):
            return True
        workers = [
            worker.thread_id
            for worker in (await sidebar_memberships([self.thread_id], workers_of=True)).values()
            if not worker.cancelled and not worker.launch_error
        ]
        if await TaskMessage.pending_threads(workers):
            return True
        reads = reads or asyncio.Semaphore(_WORKER_READS)
        checks = await asyncio.gather(
            *(RoutedWork(worker).busy(reads, visited=visited, prune=prune) for worker in workers),
            return_exceptions=True,
        )
        errors = [
            (worker, check)
            for worker, check in zip(workers, checks, strict=True)
            if isinstance(check, BaseException)
        ]
        if any(check is True for check in checks):
            for worker, error in errors:
                logger.warning(
                    "Could not check a delegated worker",
                    exc_info=error,
                    extra={"thread_id": self.thread_id, "worker_thread_id": worker},
                )
            return True
        if errors:
            raise errors[0][1]
        return await self.routed_active(visited=visited, prune=prune, metadata=metadata)

    async def busy(self, reads: asyncio.Semaphore, *, visited: frozenset[str], prune: bool) -> bool:
        # Its own workers get a bound of their own: under ``reads`` they would
        # wait for permits this check holds.
        async with reads:
            return await self.active() or await self.delegated_active(visited=visited, prune=prune)

    async def routed_active(
        self,
        *,
        visited: frozenset[str] = frozenset(),
        prune: bool = True,
        metadata: Mapping[str, object] | None = None,
    ) -> bool:
        if metadata is None:
            thread = await THREADS.get(self.thread_id)
            if thread is None:
                return False
            metadata = thread.metadata
        active = False
        for destination in self._thread_ids(metadata.get(DESTINATIONS_KEY)):
            work = RoutedWork(destination)
            busy = await work.active() or await work.delegated_active(
                visited=visited | {self.thread_id}, prune=prune
            )
            if busy:
                active = True
            elif prune:
                await self.merge(DESTINATIONS_KEY, remove=[destination])
                await work.merge(ORIGINS_KEY, remove=[self.thread_id])
        return active

    async def sync_slack_status(self) -> None:
        from openswe.slack.thinking import settle_slack_thread_status
        from openswe.source_context import SourceContext

        thread_id = self.thread_id
        context = await TaskMembership.context_for_thread(thread_id)
        if context is not None and context.membership.role == "worker":
            thread_id = context.task.require_coordinator()
        thread = await THREADS.get(thread_id)
        if thread is None:
            return
        slack_thread = SourceContext.from_metadata(thread.metadata).slack_thread
        if slack_thread is not None and slack_thread.location:
            await settle_slack_thread_status(*slack_thread.location)

    async def settle_origins(self) -> None:
        try:
            thread = await THREADS.get(self.thread_id)
            if thread is None:
                return
            for origin in self._thread_ids(thread.metadata.get(ORIGINS_KEY)):
                work = RoutedWork(origin)
                await work.routed_active()
                await work.sync_slack_status()
        except Exception:
            logger.exception(
                "Could not settle routed Slack progress",
                extra={"destination_thread_id": self.thread_id},
            )
