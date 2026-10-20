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
from openswe.tasks.service import cancel_worker, live_runs
from openswe.utils.dashboard_links import dashboard_thread_url
from openswe.utils.thread_ops import langgraph_client

type WorkerState = Literal["starting", "running", "reporting", "done", "failed", "cancelled"]

# How long a hand-off to the coordinator may keep it unsettled before it counts as lost.
HANDOFF_GRACE = timedelta(minutes=5)
_LIVE_RUN_STATUSES = frozenset({"pending", "running"})


class WorkerActivity(BaseModel):
    worker_thread_id: str
    thread_url: str | None
    instructions: str
    state: WorkerState
    run_status: str | None = None

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
            worker.state = "failed"
            return worker
        try:
            runs = await langgraph_client().runs.list(delegation.worker_thread_id, limit=1)
        except NotFoundError:
            return worker
        if not runs:
            return worker
        latest = Run.model_validate(runs[0])
        worker.run_status = latest.status
        if latest.status in _LIVE_RUN_STATUSES:
            worker.state = "running"
        elif await cls._reported(delegation, latest):
            worker.state = "done"
        else:
            worker.state = "reporting"
        return worker

    @staticmethod
    async def _reported(delegation: store.TaskDelegation, run: Run) -> bool:
        """Whether the run's outcome reached the coordinator, or can no longer be expected to."""
        if run.updated_at is None:
            return True
        ended = run.updated_at if run.updated_at.tzinfo else run.updated_at.replace(tzinfo=UTC)
        if ended < datetime.now(UTC) - HANDOFF_GRACE:
            return True
        return await TaskMessage.recorded(
            delegation.coordinator_thread_id,
            finished_delivery_id(delegation.worker_thread_id, run.run_id),
        )


class CoordinatorActivity(BaseModel):
    """A thread's live run and the workers it coordinates."""

    run_id: str | None
    awaiting_delivery: bool
    workers: list[WorkerActivity]

    @computed_field
    @property
    def settled(self) -> bool:
        """Nothing more will run on the thread unless someone prompts it."""
        return (
            self.run_id is None
            and not self.awaiting_delivery
            and not any(worker.active for worker in self.workers)
        )

    @classmethod
    async def of(cls, thread_id: str) -> Self:
        run_ids = await live_runs(thread_id)
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
            run_id=run_ids[0] if run_ids else None,
            awaiting_delivery=await TaskMessage.awaiting_delivery(
                thread_id, since=datetime.now(UTC) - HANDOFF_GRACE
            ),
            workers=[await WorkerActivity.of(delegation) for delegation in delegations],
        )

    async def cancel_workers(self) -> None:
        for worker in self.workers:
            if worker.active:
                await cancel_worker(worker.worker_thread_id)
