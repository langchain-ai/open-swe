"""PostgreSQL task authority and durable worker event outbox."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal, cast
from uuid import uuid4

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from agent.database import postgres

TaskStatus = Literal["active", "completed"]
TaskRole = Literal["coordinator", "worker"]
DelegationStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
TerminalDelegationStatus = Literal["completed", "failed", "cancelled"]


@dataclass(frozen=True, slots=True)
class TaskRecord:
    id: str
    coordinator_thread_id: str
    workspace: str
    title: str
    acceptance_criteria: list[str]
    delegated: bool
    status: TaskStatus
    completion_evidence: list[str] | None


@dataclass(frozen=True, slots=True)
class Membership:
    task_id: str
    thread_id: str
    role: TaskRole


@dataclass(frozen=True, slots=True)
class Delegation:
    id: str
    task_id: str
    coordinator_thread_id: str
    worker_thread_id: str
    instructions: str
    model: str | None
    effort: str | None
    status: DelegationStatus
    run_id: str | None


@dataclass(frozen=True, slots=True)
class WorkerDispatch:
    dispatch_key: str
    worker_thread_id: str
    content: str
    run_id: str | None
    settled: bool


@dataclass(frozen=True, slots=True)
class TaskEvent:
    id: str
    task_id: str
    worker_thread_id: str
    kind: str
    content: str
    delivered: bool


@dataclass(frozen=True, slots=True)
class _HeldLock:
    key: str
    owner: object


_HELD_LOCKS: ContextVar[tuple[_HeldLock, ...]] = ContextVar("task_store_locks", default=())


@asynccontextmanager
async def _distributed_lock(key: str) -> AsyncIterator[None]:
    owner = asyncio.current_task()
    held = _HELD_LOCKS.get()
    if any(lock.key == key and lock.owner is owner for lock in held):
        yield
        return
    lock_engine = create_async_engine(postgres.engine().url, poolclass=NullPool)
    try:
        async with lock_engine.begin() as conn:
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:subject, 0))"),
                {"subject": key},
            )
            token = _HELD_LOCKS.set((*held, _HeldLock(key, owner)))
            try:
                yield
            finally:
                _HELD_LOCKS.reset(token)
    finally:
        await lock_engine.dispose()


@asynccontextmanager
async def thread_lock(thread_id: str) -> AsyncIterator[None]:
    """Serialize all effects on a thread, including its first task/delegation transition."""
    if not postgres.configured():
        yield
        return
    async with _distributed_lock(f"task-thread:{thread_id}"):
        yield


@asynccontextmanager
async def event_delivery_lock(coordinator_thread_id: str) -> AsyncIterator[None]:
    """Serialize outbox relays; durable message IDs must also deduplicate crash retries."""
    postgres.require_configured()
    async with _distributed_lock(f"task-events:{coordinator_thread_id}"):
        yield


def _task(row: RowMapping) -> TaskRecord:
    return TaskRecord(
        id=cast(str, row["id"]),
        coordinator_thread_id=cast(str, row["coordinator_thread_id"]),
        workspace=cast(str, row["workspace"]),
        title=cast(str, row["title"]),
        acceptance_criteria=cast(list[str], row["acceptance_criteria"]),
        delegated=cast(bool, row["delegated"]),
        status=cast(TaskStatus, row["status"]),
        completion_evidence=cast(list[str] | None, row["completion_evidence"]),
    )


def _membership(row: RowMapping) -> Membership:
    return Membership(
        task_id=cast(str, row["task_id"]),
        thread_id=cast(str, row["thread_id"]),
        role=cast(TaskRole, row["role"]),
    )


def _delegation(row: RowMapping) -> Delegation:
    return Delegation(
        id=cast(str, row["id"]),
        task_id=cast(str, row["task_id"]),
        coordinator_thread_id=cast(str, row["coordinator_thread_id"]),
        worker_thread_id=cast(str, row["worker_thread_id"]),
        instructions=cast(str, row["instructions"]),
        model=cast(str | None, row["model"]),
        effort=cast(str | None, row["effort"]),
        status=cast(DelegationStatus, row["status"]),
        run_id=cast(str | None, row["run_id"]),
    )


def _event(row: RowMapping) -> TaskEvent:
    return TaskEvent(
        id=cast(str, row["id"]),
        task_id=cast(str, row["task_id"]),
        worker_thread_id=cast(str, row["worker_thread_id"]),
        kind=cast(str, row["kind"]),
        content=cast(str, row["content"]),
        delivered=cast(bool, row["delivered"]),
    )


async def _task_for_thread(conn: AsyncConnection, thread_id: str) -> TaskRecord | None:
    row = (
        (
            await conn.execute(
                text("""
                SELECT t.* FROM agent_task t
                JOIN task_membership m ON m.task_id = t.id
                WHERE m.thread_id = :thread_id
            """),
                {"thread_id": thread_id},
            )
        )
        .mappings()
        .first()
    )
    return _task(row) if row is not None else None


async def task_for_thread(thread_id: str) -> TaskRecord | None:
    if not postgres.configured():
        return None
    async with postgres.connection() as conn:
        return await _task_for_thread(conn, thread_id)


async def membership_for_thread(thread_id: str) -> Membership | None:
    if not postgres.configured():
        return None
    async with postgres.connection() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT * FROM task_membership WHERE thread_id = :thread_id"),
                    {"thread_id": thread_id},
                )
            )
            .mappings()
            .first()
        )
    return _membership(row) if row is not None else None


def _validate_criteria(title: str, acceptance_criteria: list[str]) -> None:
    if not title.strip():
        raise ValueError("A task needs a title")
    if not acceptance_criteria or any(not criterion.strip() for criterion in acceptance_criteria):
        raise ValueError("A task needs nonempty acceptance criteria")


async def _coordinator_task(conn: AsyncConnection, thread_id: str) -> TaskRecord:
    task = await _task_for_thread(conn, thread_id)
    if task is None or task.coordinator_thread_id != thread_id:
        raise PermissionError("Only the task's permanent coordinator may perform this operation")
    return task


async def ensure_task(
    thread_id: str, *, workspace: str, title: str, acceptance_criteria: list[str]
) -> TaskRecord:
    postgres.require_configured()
    _validate_criteria(title, acceptance_criteria)
    async with thread_lock(thread_id), postgres.transaction() as conn:
        existing = await _task_for_thread(conn, thread_id)
        if existing is not None:
            if existing.coordinator_thread_id != thread_id:
                raise PermissionError("Workers cannot create tasks")
            return existing
        task_id = str(uuid4())
        row = (
            (
                await conn.execute(
                    text("""
                    INSERT INTO agent_task
                        (id, coordinator_thread_id, workspace, title, acceptance_criteria)
                    VALUES (:id, :thread_id, :workspace, :title, CAST(:criteria AS jsonb))
                    RETURNING *
                """),
                    {
                        "id": task_id,
                        "thread_id": thread_id,
                        "workspace": workspace,
                        "title": title,
                        "criteria": json.dumps(acceptance_criteria),
                    },
                )
            )
            .mappings()
            .one()
        )
        await conn.execute(
            text("""
                INSERT INTO task_membership (task_id, thread_id, role)
                VALUES (:task_id, :thread_id, 'coordinator')
            """),
            {"task_id": task_id, "thread_id": thread_id},
        )
        return _task(row)


async def update_task(thread_id: str, *, title: str, acceptance_criteria: list[str]) -> TaskRecord:
    postgres.require_configured()
    _validate_criteria(title, acceptance_criteria)
    async with thread_lock(thread_id), postgres.transaction() as conn:
        task = await _coordinator_task(conn, thread_id)
        if task.status != "active":
            raise ValueError("Completed tasks cannot be changed")
        row = (
            (
                await conn.execute(
                    text("""
                    UPDATE agent_task SET title = :title, acceptance_criteria = CAST(:criteria AS jsonb)
                    WHERE id = :id RETURNING *
                """),
                    {"id": task.id, "title": title, "criteria": json.dumps(acceptance_criteria)},
                )
            )
            .mappings()
            .one()
        )
        return _task(row)


async def complete_task(thread_id: str, *, evidence: list[str]) -> TaskRecord:
    postgres.require_configured()
    async with thread_lock(thread_id), postgres.transaction() as conn:
        task = await _coordinator_task(conn, thread_id)
        if len(evidence) != len(task.acceptance_criteria) or any(
            not item.strip() for item in evidence
        ):
            raise ValueError("Provide nonempty completion evidence for every acceptance criterion")
        active = await conn.scalar(
            text("""
                SELECT EXISTS (
                    SELECT 1 FROM task_delegation
                    WHERE task_id = :id AND status IN ('pending', 'running')
                ) OR EXISTS (
                    SELECT 1 FROM task_worker_dispatch r
                    JOIN task_delegation d ON d.worker_thread_id = r.worker_thread_id
                    WHERE d.task_id = :id AND NOT r.settled
                )
            """),
            {"id": task.id},
        )
        if active:
            raise ValueError("Cannot complete a task while workers are pending or running")
        row = (
            (
                await conn.execute(
                    text("""
                    UPDATE agent_task SET status = 'completed', completion_evidence = CAST(:evidence AS jsonb)
                    WHERE id = :id RETURNING *
                """),
                    {"id": task.id, "evidence": json.dumps(evidence)},
                )
            )
            .mappings()
            .one()
        )
        return _task(row)


async def create_delegation(
    coordinator_thread_id: str,
    *,
    worker_thread_id: str,
    instructions: str,
    model: str | None,
    effort: str | None,
) -> Delegation:
    postgres.require_configured()
    if not instructions.strip():
        raise ValueError("Delegation needs instructions")
    if worker_thread_id == coordinator_thread_id:
        raise PermissionError("A coordinator cannot delegate to itself")
    async with thread_lock(coordinator_thread_id), postgres.transaction() as conn:
        task = await _coordinator_task(conn, coordinator_thread_id)
        if task.status != "active":
            raise ValueError("Only active tasks accept delegation")
        if await _task_for_thread(conn, worker_thread_id) is not None:
            raise ValueError("The worker already belongs to a task")
        await conn.execute(
            text("""
                INSERT INTO task_membership (task_id, thread_id, role)
                VALUES (:task_id, :thread_id, 'worker')
            """),
            {"task_id": task.id, "thread_id": worker_thread_id},
        )
        row = (
            (
                await conn.execute(
                    text("""
                    INSERT INTO task_delegation
                        (id, task_id, coordinator_thread_id, worker_thread_id, instructions, model, effort)
                    VALUES (:id, :task_id, :coordinator_thread_id, :worker_thread_id,
                            :instructions, :model, :effort) RETURNING *
                """),
                    {
                        "id": str(uuid4()),
                        "task_id": task.id,
                        "coordinator_thread_id": coordinator_thread_id,
                        "worker_thread_id": worker_thread_id,
                        "instructions": instructions,
                        "model": model,
                        "effort": effort,
                    },
                )
            )
            .mappings()
            .one()
        )
        return _delegation(row)


async def delegation_for_worker(worker_thread_id: str) -> Delegation | None:
    if not postgres.configured():
        return None
    async with postgres.connection() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT * FROM task_delegation WHERE worker_thread_id = :thread_id"),
                    {"thread_id": worker_thread_id},
                )
            )
            .mappings()
            .first()
        )
    return _delegation(row) if row is not None else None


async def list_delegations(thread_id: str) -> list[Delegation]:
    if not postgres.configured():
        return []
    async with postgres.connection() as conn:
        rows = (
            (
                await conn.execute(
                    text("""
                    SELECT d.* FROM task_delegation d
                    JOIN task_membership m ON m.task_id = d.task_id
                    WHERE m.thread_id = :thread_id ORDER BY d.created_at, d.id
                """),
                    {"thread_id": thread_id},
                )
            )
            .mappings()
            .all()
        )
    return [_delegation(row) for row in rows]


async def set_delegation_run(worker_thread_id: str, run_id: str) -> None:
    postgres.require_configured()
    delegation = await delegation_for_worker(worker_thread_id)
    if delegation is None:
        raise ValueError("Worker delegation not found")
    async with thread_lock(delegation.coordinator_thread_id), postgres.transaction() as conn:
        task = await _coordinator_task(conn, delegation.coordinator_thread_id)
        if task.status != "active":
            raise ValueError("Completed tasks cannot start worker runs")
        await conn.execute(
            text("""
                UPDATE task_delegation SET
                    status = CASE WHEN run_id = :run_id THEN status ELSE 'running' END,
                    run_id = :run_id
                WHERE worker_thread_id = :thread_id
            """),
            {"thread_id": worker_thread_id, "run_id": run_id},
        )


async def finish_delegation(
    worker_thread_id: str, *, status: TerminalDelegationStatus, run_id: str | None = None
) -> None:
    postgres.require_configured()
    if status not in {"completed", "failed", "cancelled"}:
        raise ValueError("Expected a terminal worker status")
    delegation = await delegation_for_worker(worker_thread_id)
    if delegation is None:
        raise ValueError("Worker delegation not found")
    async with thread_lock(delegation.coordinator_thread_id), postgres.transaction() as conn:
        await conn.execute(
            text("""
                UPDATE task_delegation SET status = :status, run_id = COALESCE(run_id, :run_id)
                WHERE worker_thread_id = :thread_id
                    AND (CAST(:run_id AS text) IS NULL OR run_id IS NULL OR run_id = :run_id)
                    AND status IN ('pending', 'running')
            """),
            {"thread_id": worker_thread_id, "status": status, "run_id": run_id},
        )


async def record_event(
    worker_thread_id: str, *, event_key: str, kind: str, content: str
) -> TaskEvent:
    postgres.require_configured()
    if not event_key.strip() or not kind.strip():
        raise ValueError("An event needs a deduplication key and kind")
    async with postgres.transaction() as conn:
        row = (
            (
                await conn.execute(
                    text("""
                    INSERT INTO task_event (id, task_id, worker_thread_id, event_key, kind, content)
                    SELECT :id, task_id, worker_thread_id, :event_key, :kind, :content
                    FROM task_delegation WHERE worker_thread_id = :thread_id
                    ON CONFLICT (worker_thread_id, event_key)
                    DO UPDATE SET event_key = task_event.event_key RETURNING *
                """),
                    {
                        "id": str(uuid4()),
                        "thread_id": worker_thread_id,
                        "event_key": event_key,
                        "kind": kind,
                        "content": content,
                    },
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValueError("Worker delegation not found")
        return _event(row)


async def pending_events(coordinator_thread_id: str) -> list[TaskEvent]:
    if not postgres.configured():
        return []
    async with postgres.connection() as conn:
        await _coordinator_task(conn, coordinator_thread_id)
        rows = (
            (
                await conn.execute(
                    text("""
                    SELECT e.* FROM task_event e
                    JOIN agent_task t ON t.id = e.task_id
                    WHERE t.coordinator_thread_id = :thread_id AND NOT e.delivered
                    ORDER BY e.created_at, e.id
                """),
                    {"thread_id": coordinator_thread_id},
                )
            )
            .mappings()
            .all()
        )
    return [_event(row) for row in rows]


async def mark_event_delivered(event_id: str) -> None:
    postgres.require_configured()
    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE task_event SET delivered = true WHERE id = :id"),
            {"id": event_id},
        )


async def coordinators_with_pending_events() -> list[str]:
    if not postgres.configured():
        return []
    async with postgres.connection() as conn:
        rows = await conn.execute(
            text("""
                SELECT DISTINCT t.coordinator_thread_id FROM agent_task t
                JOIN task_event e ON e.task_id = t.id WHERE NOT e.delivered
                ORDER BY t.coordinator_thread_id
            """)
        )
        return [cast(str, row[0]) for row in rows]


def _worker_dispatch(row: RowMapping) -> WorkerDispatch:
    return WorkerDispatch(
        dispatch_key=cast(str, row["dispatch_key"]),
        worker_thread_id=cast(str, row["worker_thread_id"]),
        content=cast(str, row["content"]),
        run_id=cast(str | None, row["run_id"]),
        settled=cast(bool, row["settled"]),
    )


async def record_worker_dispatch(
    worker_thread_id: str, *, dispatch_key: str, content: str
) -> WorkerDispatch:
    postgres.require_configured()
    if not dispatch_key.strip() or not content.strip():
        raise ValueError("A worker dispatch needs a stable identity and content")
    delegation = await delegation_for_worker(worker_thread_id)
    if delegation is None:
        raise ValueError("Worker delegation not found")
    async with thread_lock(delegation.coordinator_thread_id), postgres.transaction() as conn:
        task = await _coordinator_task(conn, delegation.coordinator_thread_id)
        if task.status != "active":
            raise ValueError("Completed tasks cannot start worker runs")
        row = (
            (
                await conn.execute(
                    text("""
                        INSERT INTO task_worker_dispatch (dispatch_key, worker_thread_id, content)
                        VALUES (:key, :worker, :content)
                        ON CONFLICT (dispatch_key)
                        DO UPDATE SET dispatch_key = task_worker_dispatch.dispatch_key RETURNING *
                    """),
                    {"key": dispatch_key, "worker": worker_thread_id, "content": content},
                )
            )
            .mappings()
            .one()
        )
        result = _worker_dispatch(row)
        if result.worker_thread_id != worker_thread_id or result.content != content:
            raise ValueError("This dispatch identity already belongs to different work")
        if result.run_id is None and not result.settled:
            await conn.execute(
                text("""
                    UPDATE task_delegation SET status = 'pending'
                    WHERE worker_thread_id = :worker AND status <> 'running'
                """),
                {"worker": worker_thread_id},
            )
        return result


async def register_worker_dispatch(worker_thread_id: str, dispatch_key: str, run_id: str) -> None:
    postgres.require_configured()
    async with postgres.transaction() as conn:
        registered = await conn.scalar(
            text("""
                UPDATE task_worker_dispatch SET run_id = :run_id
                WHERE worker_thread_id = :worker AND dispatch_key = :key
                    AND (run_id IS NULL OR run_id = :run_id)
                RETURNING dispatch_key
            """),
            {"worker": worker_thread_id, "key": dispatch_key, "run_id": run_id},
        )
        if registered is None:
            raise ValueError("The run does not match this worker's persisted dispatch")


async def settle_worker_dispatch(worker_thread_id: str, run_id: str) -> None:
    postgres.require_configured()
    async with postgres.transaction() as conn:
        await conn.execute(
            text("""
                UPDATE task_worker_dispatch SET settled = true
                WHERE worker_thread_id = :worker AND run_id = :run_id
            """),
            {"worker": worker_thread_id, "run_id": run_id},
        )


async def cancel_pending_dispatches(worker_thread_id: str) -> None:
    postgres.require_configured()
    async with postgres.transaction() as conn:
        await conn.execute(
            text("""
                UPDATE task_worker_dispatch SET settled = true
                WHERE worker_thread_id = :worker AND run_id IS NULL
            """),
            {"worker": worker_thread_id},
        )


async def task_dispatch_invocation(thread_id: str, dispatch_key: str) -> str | None:
    if not postgres.configured():
        return None
    async with postgres.connection() as conn:
        winner = await conn.scalar(
            text("""
                SELECT invocation_id FROM task_dispatch_receipt
                WHERE thread_id = :thread_id AND dispatch_key = :dispatch_key
            """),
            {"thread_id": thread_id, "dispatch_key": dispatch_key},
        )
        return cast(str | None, winner)


async def claim_task_dispatch(thread_id: str, dispatch_key: str, invocation_id: str) -> bool:
    postgres.require_configured()
    if not thread_id or not dispatch_key or not invocation_id:
        raise ValueError(
            "A task dispatch receipt requires thread, dispatch, and invocation identities"
        )
    async with postgres.transaction() as conn:
        winner = await conn.scalar(
            text("""
                INSERT INTO task_dispatch_receipt (thread_id, dispatch_key, invocation_id)
                SELECT m.thread_id, :dispatch_key, :invocation_id
                FROM task_membership m
                JOIN agent_task t ON t.id = m.task_id
                WHERE m.thread_id = :thread_id AND (
                    (m.role = 'worker' AND EXISTS (
                        SELECT 1 FROM task_worker_dispatch d
                        WHERE d.worker_thread_id = m.thread_id
                            AND d.dispatch_key = :dispatch_key
                    )) OR
                    (m.role = 'coordinator' AND t.coordinator_thread_id = m.thread_id
                        AND EXISTS (
                            SELECT 1 FROM task_event e
                            WHERE e.task_id = m.task_id
                                AND 'task-event:' || e.id = :dispatch_key
                        ))
                )
                ON CONFLICT (thread_id, dispatch_key)
                DO UPDATE SET invocation_id = task_dispatch_receipt.invocation_id
                RETURNING invocation_id
            """),
            {
                "thread_id": thread_id,
                "dispatch_key": dispatch_key,
                "invocation_id": invocation_id,
            },
        )
        if winner is None:
            raise PermissionError("No persisted task dispatch matches this thread and dispatch key")
        return winner == invocation_id


async def pending_worker_dispatches() -> list[WorkerDispatch]:
    if not postgres.configured():
        return []
    async with postgres.connection() as conn:
        rows = (
            (
                await conn.execute(
                    text("""
                        SELECT * FROM task_worker_dispatch WHERE NOT settled
                        ORDER BY created_at, dispatch_key
                    """)
                )
            )
            .mappings()
            .all()
        )
        return [_worker_dispatch(row) for row in rows]
