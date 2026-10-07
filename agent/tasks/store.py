from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid7

from sqlalchemy import ForeignKey, Text, select, text, update
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agent.database import postgres
from agent.database.orm import Base
from agent.workspaces.rows import WorkspaceRow

type TaskRole = Literal["coordinator", "worker"]


class Task(Base):
    __tablename__ = "task"

    title: Mapped[str]
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"))
    workspace: Mapped[WorkspaceRow] = relationship(init=False, lazy="joined", innerjoin=True)
    coordinator_thread_id: Mapped[str | None] = mapped_column(default=None)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    delegated: Mapped[bool] = mapped_column(default=False)

    def require_coordinator(self) -> str:
        if self.coordinator_thread_id is None:
            raise RuntimeError("Task has no coordinator")
        return self.coordinator_thread_id

    @classmethod
    async def reserve_worker(
        cls,
        coordinator_thread_id: str,
        worker_thread_id: str,
        *,
        title: str,
        workspace: str,
        instructions: str,
        model: str,
        effort: str,
    ) -> tuple[Task, TaskDelegation]:
        postgres.require_configured()
        async with postgres.session() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"task-delegation:{coordinator_thread_id}"},
            )
            workspace_row = await session.scalar(
                select(WorkspaceRow).where(WorkspaceRow.slug == workspace)
            )
            if workspace_row is None:
                raise ValueError("The task's workspace no longer exists")
            membership = await session.get(TaskMembership, coordinator_thread_id)
            if membership is None:
                task = cls(
                    coordinator_thread_id=coordinator_thread_id,
                    title=title,
                    workspace_id=workspace_row.id,
                )
                task.workspace = workspace_row
                session.add(task)
                await session.flush()
                session.add(
                    TaskMembership(
                        thread_id=coordinator_thread_id, task_id=task.id, role="coordinator"
                    )
                )
            else:
                task = await session.get(cls, membership.task_id, with_for_update={"of": cls})
                if (
                    task is None
                    or membership.role != "coordinator"
                    or task.coordinator_thread_id != coordinator_thread_id
                ):
                    raise PermissionError("Only the coordinator can delegate")
                if task.workspace_id != workspace_row.id:
                    raise PermissionError("The thread no longer belongs to the task's workspace")
            existing = await session.get(TaskDelegation, worker_thread_id)
            if existing is not None:
                if (
                    existing.task_id != task.id
                    or existing.coordinator_thread_id != coordinator_thread_id
                ):
                    raise PermissionError("Worker identity belongs to another task")
                return task, existing
            task.delegated = True
            session.add(TaskMembership(thread_id=worker_thread_id, task_id=task.id, role="worker"))
            await session.flush()
            delegation = TaskDelegation(
                worker_thread_id=worker_thread_id,
                task_id=task.id,
                coordinator_thread_id=coordinator_thread_id,
                instructions=instructions,
                model=model,
                effort=effort,
            )
            session.add(delegation)
            return task, delegation


class TaskMembership(Base):
    __tablename__ = "task_membership"

    thread_id: Mapped[str] = mapped_column(primary_key=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("task.id", ondelete="CASCADE"))
    role: Mapped[TaskRole] = mapped_column(Text)

    @classmethod
    async def context_for_thread(cls, thread_id: str) -> TaskContext | None:
        if not postgres.configured():
            return None
        async with postgres.session() as session:
            membership = await session.get(cls, thread_id)
            if membership is None:
                return None
            task = await session.get(Task, membership.task_id)
            if task is None:
                raise RuntimeError("Task membership has no task")
            return TaskContext(task, membership)


class TaskDelegation(Base):
    __tablename__ = "task_delegation"

    worker_thread_id: Mapped[str] = mapped_column(primary_key=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("task.id", ondelete="CASCADE"))
    coordinator_thread_id: Mapped[str]
    instructions: Mapped[str]
    model: Mapped[str]
    effort: Mapped[str | None]
    launch_error: Mapped[str | None] = mapped_column(default=None)
    cancelled: Mapped[bool] = mapped_column(default=False)

    @classmethod
    async def get(cls, worker_thread_id: str) -> TaskDelegation | None:
        if not postgres.configured():
            return None
        async with postgres.session() as session:
            return await session.get(cls, worker_thread_id)

    @classmethod
    async def for_task(cls, task_id: UUID) -> list[TaskDelegation]:
        async with postgres.session() as session:
            return list(
                await session.scalars(
                    select(cls).where(cls.task_id == task_id).order_by(cls.worker_thread_id)
                )
            )

    @classmethod
    async def set_launch_error(cls, worker_thread_id: str, error: str | None) -> None:
        async with postgres.session() as session:
            await session.execute(
                update(cls)
                .where(cls.worker_thread_id == worker_thread_id)
                .values(launch_error=error)
            )


@dataclass(frozen=True)
class TaskContext:
    task: Task
    membership: TaskMembership


@dataclass(frozen=True)
class SidebarTaskMembership:
    thread_id: str
    task_id: str
    role: TaskRole
    coordinator_thread_id: str
    instructions: str = ""
    cancelled: bool = False
    launch_error: bool = False


async def sidebar_memberships(
    thread_ids: Sequence[str], *, workers_of: bool = False
) -> dict[str, SidebarTaskMembership]:
    """Read authoritative task relationships in batches, without thread metadata."""
    if not thread_ids or not postgres.configured():
        return {}
    memberships: dict[str, SidebarTaskMembership] = {}
    async with postgres.session() as session:
        for offset in range(0, len(thread_ids), 1000):
            ids = thread_ids[offset : offset + 1000]
            statement = (
                select(TaskMembership, Task, TaskDelegation)
                .join(Task, Task.id == TaskMembership.task_id)
                .outerjoin(
                    TaskDelegation,
                    (TaskDelegation.worker_thread_id == TaskMembership.thread_id)
                    & (TaskDelegation.task_id == TaskMembership.task_id)
                    & (TaskDelegation.coordinator_thread_id == Task.coordinator_thread_id),
                )
            )
            if workers_of:
                statement = statement.where(
                    Task.coordinator_thread_id.in_(ids),
                    TaskMembership.role == "worker",
                )
            else:
                statement = statement.where(TaskMembership.thread_id.in_(ids))
            for membership, task, delegation in await session.execute(statement):
                if task.coordinator_thread_id is None or (
                    membership.role == "coordinator"
                    and membership.thread_id != task.coordinator_thread_id
                ):
                    continue
                memberships[membership.thread_id] = SidebarTaskMembership(
                    thread_id=membership.thread_id,
                    task_id=str(task.id),
                    role=membership.role,
                    coordinator_thread_id=task.coordinator_thread_id,
                    instructions=delegation.instructions if delegation else "",
                    cancelled=delegation.cancelled if delegation else False,
                    launch_error=bool(delegation and delegation.launch_error),
                )
    return memberships
