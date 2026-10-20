"""What a coordinator's task is still doing, for a client that waits for it to settle.

The ``oswe`` CLI serves its thread's sandbox from the user's machine, and workers
share that sandbox, so it keeps serving until the coordinator has nothing left
to run: no live run, no task message on its way, and no worker that may still
report.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal, Self

from langgraph_sdk.errors import NotFoundError
from pydantic import BaseModel, computed_field

from openswe.tasks import store
from openswe.tasks.events import finished_delivery_id
from openswe.tasks.messages import TaskMessage
from openswe.tasks.schemas import Run
from openswe.utils.dashboard_links import dashboard_thread_url
from openswe.utils.thread_ops import langgraph_client

type WorkerState = Literal["starting", "running", "reporting", "done", "failed", "cancelled"]

# How long a worker may take to start, or a hand-off to reach the coordinator, before it is lost.
LOST_AFTER = timedelta(minutes=5)
_RUNS_PAGE = 100


class WorkerActivity(BaseModel):
    worker_thread_id: str
    thread_url: str | None
    instructions: str
    state: WorkerState
    run_status: str | None = None
    error: str | None = None

    @computed_field
    @property
    def active(self) -> bool:
        return self.state in {"starting", "running", "reporting"}

    @classmethod
    async def of(cls, delegation: store.TaskDelegation) -> Self:
        worker = cls(
            worker_thread_id=delegation.worker_thread_id,
            thread_url=dashboard_thread_url(delegation.worker_thread_id),
            instructions=delegation.instructions,
            state="cancelled" if delegation.cancelled else "starting",
        )
        if delegation.cancelled:
            return worker
        if delegation.launch_error:
            return worker.failed(delegation.launch_error)
        try:
            runs = await langgraph_client().runs.list(delegation.worker_thread_id, limit=1)
        except NotFoundError:
            runs = []
        if not runs:
            if delegation.created_at < datetime.now(UTC) - LOST_AFTER:
                return worker.failed(
                    f"The worker did not start within {LOST_AFTER.seconds // 60} minutes."
                )
            return worker
        latest = Run.model_validate(runs[0])
        worker.run_status = latest.status
        if latest.live:
            worker.state = "running"
        elif await cls._reported(delegation, latest):
            worker.state = "done"
        else:
            worker.state = "reporting"
        return worker

    def failed(self, error: str) -> Self:
        self.state = "failed"
        self.error = error
        return self

    @staticmethod
    async def _reported(delegation: store.TaskDelegation, run: Run) -> bool:
        """Whether the run's outcome reached the coordinator, or can no longer be expected to."""
        ended = run.ended_at
        if ended is None or ended < datetime.now(UTC) - LOST_AFTER:
            return True
        return await TaskMessage.recorded(
            delegation.coordinator_thread_id,
            finished_delivery_id(delegation.worker_thread_id, run.run_id),
        )


class CoordinatorActivity(BaseModel):
    """A thread's runs since the one a client last followed, and the workers it coordinates."""

    runs: list[str]
    running: bool
    awaiting_delivery: bool
    workers: list[WorkerActivity]

    @computed_field
    @property
    def settled(self) -> bool:
        """Nothing more will run on the thread unless someone prompts it."""
        return (
            not self.running
            and not self.awaiting_delivery
            and not any(worker.active for worker in self.workers)
        )

    @classmethod
    async def of(cls, thread_id: str, *, after: str | None = None) -> Self:
        """The thread's activity; ``runs`` are the ids of those created after ``after``."""
        newer, latest = await cls._runs_after(thread_id, after)
        running = latest is not None and latest.live
        lost_before = datetime.now(UTC) - LOST_AFTER
        idle_since = latest.ended_at if latest is not None else None
        # A message held back while the coordinator ran could not be delivered before that run ended.
        recently_deliverable = running or (idle_since is not None and idle_since >= lost_before)
        context = await store.TaskMembership.context_for_thread(thread_id)
        delegations = (
            [
                delegation
                for delegation in await store.TaskDelegation.for_task(context.task.id)
                if delegation.coordinator_thread_id == thread_id
            ]
            if context is not None and context.membership.role == "coordinator"
            else []
        )
        return cls(
            runs=[run.run_id for run in reversed(newer)],
            running=running,
            awaiting_delivery=await TaskMessage.awaiting_delivery(
                thread_id, since=None if recently_deliverable else lost_before
            ),
            workers=[await WorkerActivity.of(delegation) for delegation in delegations],
        )

    @staticmethod
    async def _runs_after(thread_id: str, after: str | None) -> tuple[list[Run], Run | None]:
        """The thread's runs created after ``after``, newest first, and its latest run.

        Without ``after``, or when ``after`` is not among the thread's runs, only the latest is new.
        """
        client = langgraph_client()
        newer: list[Run] = []
        limit = 1 if after is None else _RUNS_PAGE
        offset = 0
        while True:
            page = await client.runs.list(thread_id, limit=limit, offset=offset)
            for run in map(Run.model_validate, page):
                if run.run_id == after:
                    return newer, newer[0] if newer else run
                newer.append(run)
            if len(page) < limit or after is None:
                return newer[:1], newer[0] if newer else None
            offset += limit

    async def stop_workers(self) -> None:
        """Cancel the workers still active, as a person stopping the task: they report nothing."""
        # The webapp must not import the agent stack at startup.
        from openswe.tasks.service import cancel_worker

        for worker in self.workers:
            if worker.active:
                await cancel_worker(worker.worker_thread_id, silently=True)
