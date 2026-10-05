from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid7

from pydantic import JsonValue
from sqlalchemy import Text, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agent.database import postgres
from agent.database.orm import Base

type TaskStatus = Literal["active", "completed"]
type TaskRole = Literal["coordinator", "worker"]


class CoordinatedTask(Base):
    __tablename__ = "coordinated_task"

    coordinator_thread_id: Mapped[str]
    title: Mapped[str]
    acceptance_criteria: Mapped[list[str]] = mapped_column(JSONB)
    workspace: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    status: Mapped[TaskStatus] = mapped_column(Text, default="active")
    assessment: Mapped[list[dict[str, JsonValue]]] = mapped_column(JSONB, default_factory=list)
    delegated: Mapped[bool] = mapped_column(default=False)
    revision: Mapped[int] = mapped_column(default=1)


class TaskMembership(Base):
    __tablename__ = "task_membership"

    thread_id: Mapped[str] = mapped_column(primary_key=True)
    task_id: Mapped[UUID]
    role: Mapped[TaskRole] = mapped_column(Text)


class TaskDelegation(Base):
    __tablename__ = "task_delegation"

    worker_thread_id: Mapped[str] = mapped_column(primary_key=True)
    task_id: Mapped[UUID]
    coordinator_thread_id: Mapped[str]
    instructions: Mapped[str]
    model: Mapped[str]
    effort: Mapped[str | None]
    launch_error: Mapped[str | None] = mapped_column(default=None)
    cancelled: Mapped[bool] = mapped_column(default=False)


@dataclass(frozen=True)
class TaskContext:
    task: CoordinatedTask
    membership: TaskMembership


async def load_context(thread_id: str) -> TaskContext | None:
    if not postgres.configured():
        return None
    async with postgres.session() as session:
        membership = await session.get(TaskMembership, thread_id)
        if membership is None:
            return None
        task = await session.get(CoordinatedTask, membership.task_id)
        if task is None:
            raise RuntimeError("Task membership has no task")
        return TaskContext(task, membership)


async def get_delegation(worker_thread_id: str) -> TaskDelegation | None:
    if not postgres.configured():
        return None
    async with postgres.session() as session:
        return await session.get(TaskDelegation, worker_thread_id)


async def list_delegations(task_id: UUID) -> list[TaskDelegation]:
    async with postgres.session() as session:
        return list(
            await session.scalars(
                select(TaskDelegation)
                .where(TaskDelegation.task_id == task_id)
                .order_by(TaskDelegation.worker_thread_id)
            )
        )


async def configure(
    thread_id: str, *, title: str, acceptance_criteria: list[str], workspace: str
) -> CoordinatedTask:
    title = title.strip()
    criteria = [criterion.strip() for criterion in acceptance_criteria]
    if not title or not criteria or any(not criterion for criterion in criteria):
        raise ValueError("A title and nonempty acceptance criteria are required")
    if len(criteria) != len(set(criteria)):
        raise ValueError("Acceptance criteria must be distinct")
    postgres.require_configured()
    async with postgres.session() as session:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"task-configure:{thread_id}"},
        )
        membership = await session.get(TaskMembership, thread_id)
        if membership is None:
            task = CoordinatedTask(
                coordinator_thread_id=thread_id,
                title=title,
                acceptance_criteria=criteria,
                workspace=workspace,
            )
            session.add(task)
            await session.flush()
            session.add(TaskMembership(thread_id=thread_id, task_id=task.id, role="coordinator"))
        else:
            task = await session.get(CoordinatedTask, membership.task_id, with_for_update=True)
            if (
                task is None
                or membership.role != "coordinator"
                or task.coordinator_thread_id != thread_id
            ):
                raise PermissionError("Only the permanent coordinator can configure a task")
            if task.title != title or task.acceptance_criteria != criteria:
                task.title = title
                task.acceptance_criteria = criteria
                task.status = "active"
                task.assessment = []
                task.revision += 1
        return task


async def reserve_worker(
    task_id: UUID,
    coordinator_thread_id: str,
    worker_thread_id: str,
    *,
    instructions: str,
    model: str,
    effort: str,
) -> TaskDelegation:
    async with postgres.session() as session:
        task = await session.get(CoordinatedTask, task_id, with_for_update=True)
        if task is None or task.coordinator_thread_id != coordinator_thread_id:
            raise PermissionError("Only the permanent coordinator can delegate")
        existing = await session.get(TaskDelegation, worker_thread_id)
        if existing is not None:
            if (
                existing.task_id != task_id
                or existing.coordinator_thread_id != coordinator_thread_id
            ):
                raise PermissionError("Worker identity belongs to another task")
            return existing
        if task.status != "active":
            raise ValueError("Reconfigure the completed task before assigning more work")
        task.delegated = True
        task.assessment = []
        task.revision += 1
        session.add(TaskMembership(thread_id=worker_thread_id, task_id=task_id, role="worker"))
        await session.flush()
        delegation = TaskDelegation(
            worker_thread_id=worker_thread_id,
            task_id=task_id,
            coordinator_thread_id=coordinator_thread_id,
            instructions=instructions,
            model=model,
            effort=effort,
        )
        session.add(delegation)
        return delegation


async def set_launch_error(worker_thread_id: str, error: str | None) -> None:
    async with postgres.session() as session:
        await session.execute(
            update(TaskDelegation)
            .where(TaskDelegation.worker_thread_id == worker_thread_id)
            .values(launch_error=error)
        )


async def assess(
    task_id: UUID,
    coordinator_thread_id: str,
    *,
    revision: int,
    evidence: list[str],
    completed: bool,
) -> CoordinatedTask:
    async with postgres.session() as session:
        task = await session.get(CoordinatedTask, task_id, with_for_update=True)
        if task is None or task.coordinator_thread_id != coordinator_thread_id:
            raise PermissionError("Only the permanent coordinator can assess completion")
        if task.revision != revision:
            raise ValueError("The task changed; read its current criteria and assess again")
        if len(evidence) != len(task.acceptance_criteria) or any(
            not item.strip() for item in evidence
        ):
            raise ValueError("Provide evidence for each acceptance criterion, in order")
        task.assessment = [
            {"criterion": criterion, "evidence": item.strip()}
            for criterion, item in zip(task.acceptance_criteria, evidence, strict=True)
        ]
        task.status = "completed" if completed else "active"
        task.revision += 1
        return task
