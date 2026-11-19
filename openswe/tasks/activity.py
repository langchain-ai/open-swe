"""What a coordinator's task is still doing, for a client that waits for it to settle.

The ``oswe`` CLI serves its thread's sandbox from the user's machine, and workers
share that sandbox, so it keeps serving until the coordinator has nothing left
to run: no live run, no task message on its way, no worker that may still
report, and no background command or monitor whose completion wakes it.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal, Self

from langgraph_sdk.errors import NotFoundError
from pydantic import BaseModel, computed_field

from openswe.database import postgres
from openswe.tasks import store
from openswe.tasks.events import finished_delivery_id
from openswe.tasks.messages import TaskMessage
from openswe.tasks.schemas import Run
from openswe.threads.store import THREADS
from openswe.utils.background_task_state import RUNNING_BACKGROUND_TASKS_KEY, RUNNING_MONITORS_KEY
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
        if delegation.cancelled or delegation.launch_error:
            return cls.from_runs(delegation, [], reported=False)
        try:
            runs, _latest = await CoordinatorActivity.runs_after(delegation.worker_thread_id, None)
        except NotFoundError:
            runs = []
        parsed = [Run.model_validate(run) for run in runs]
        reported = (
            await cls._reported(delegation, parsed[0]) if parsed and not parsed[0].live else False
        )
        return cls.from_runs(delegation, parsed, reported=reported)

    @classmethod
    def from_runs(
        cls, delegation: store.TaskDelegation, runs: list[Run], *, reported: bool | None
    ) -> Self:
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
        if not runs:
            if delegation.created_at < datetime.now(UTC) - LOST_AFTER:
                return worker.failed(
                    f"The worker did not start within {LOST_AFTER.seconds // 60} minutes."
                )
            return worker
        latest = runs[0]
        worker.run_status = latest.status
        if latest.live:
            worker.state = "running"
        elif (
            reported or latest.ended_at is None or latest.ended_at < datetime.now(UTC) - LOST_AFTER
        ):
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
    background_work: bool
    workers: list[WorkerActivity]

    @computed_field
    @property
    def settled(self) -> bool:
        """Nothing more will run on the thread unless someone prompts it."""
        return (
            not self.running
            and not self.awaiting_delivery
            and not self.background_work
            and not any(worker.active for worker in self.workers)
        )

    @staticmethod
    async def has_background_work(thread_id: str) -> bool:
        """Whether a background command or monitor the thread started will wake it when done."""
        thread = await THREADS.get(thread_id)
        metadata = thread.metadata if thread is not None else {}
        return any(
            isinstance(tracked := metadata.get(key), list) and bool(tracked)
            for key in (RUNNING_BACKGROUND_TASKS_KEY, RUNNING_MONITORS_KEY)
        )

    @classmethod
    async def of(cls, thread_id: str, *, after: str | None = None) -> Self:
        """The thread's activity; ``runs`` are the ids of those created after ``after``."""
        # Read before the runs: a finished command stays tracked until its completion run exists.
        background_work = await cls.has_background_work(thread_id)
        newer, latest = await cls.runs_after(thread_id, after)
        running = latest is not None and latest.live
        lost_before = datetime.now(UTC) - LOST_AFTER
        idle_since = latest.ended_at if latest is not None else None
        # A message held back while the coordinator ran could not be delivered before that run ended.
        recently_deliverable = running or (idle_since is not None and idle_since >= lost_before)
        async with postgres.session() as session:
            context = await store.TaskMembership.context_for_thread(thread_id, session=session)
            delegations = (
                [
                    delegation
                    for delegation in await store.TaskDelegation.for_task(
                        context.task.id, session=session
                    )
                    if delegation.coordinator_thread_id == thread_id
                ]
                if context is not None and context.membership.role == "coordinator"
                else []
            )
        # Runtime reads own no database scope; receipts are read once afterwards.
        worker_runs: list[list[Run]] = []
        for delegation in delegations:
            runs: list[Run] = []
            if not delegation.cancelled and not delegation.launch_error:
                try:
                    runs, _latest = await cls.runs_after(delegation.worker_thread_id, None)
                except NotFoundError:
                    runs = []
            worker_runs.append(runs)
        deliveries = {
            finished_delivery_id(delegation.worker_thread_id, runs[0].run_id)
            for delegation, runs in zip(delegations, worker_runs, strict=True)
            if runs and not runs[0].live
        }
        async with postgres.session() as session:
            awaiting = await TaskMessage.awaiting_delivery(
                thread_id, since=None if recently_deliverable else lost_before, session=session
            )
            recorded = await TaskMessage.recorded_deliveries(thread_id, deliveries, session=session)
        return cls(
            runs=[run.run_id for run in reversed(newer)],
            running=running,
            awaiting_delivery=awaiting,
            background_work=background_work,
            workers=[
                WorkerActivity.from_runs(
                    delegation,
                    runs,
                    reported=bool(
                        runs
                        and finished_delivery_id(delegation.worker_thread_id, runs[0].run_id)
                        in recorded
                    ),
                )
                for delegation, runs in zip(delegations, worker_runs, strict=True)
            ],
        )

    @staticmethod
    async def runs_after(thread_id: str, after: str | None) -> tuple[list[Run], Run | None]:
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
