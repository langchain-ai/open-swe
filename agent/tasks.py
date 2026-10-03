"""Durable task membership and execution authority."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from agent.database import postgres


@dataclass
class _Lease:
    thread_id: str
    exclusive: bool
    active: bool = True


_HELD: ContextVar[_Lease | None] = ContextVar("task_authority", default=None)
logger = logging.getLogger(__name__)


@dataclass
class _Owner:
    id: UUID
    connection: AsyncConnection
    tasks: set[asyncio.Task[object]]
    monitor: asyncio.Task[None] | None = None
    failed: bool = False


_OWNER: _Owner | None = None
_OWNER_GATE = asyncio.Lock()


async def _watch_owner(owner: _Owner) -> None:
    try:
        while True:
            await asyncio.sleep(1)
            await asyncio.wait_for(owner.connection.execute(text("SELECT 1")), timeout=5)
    except asyncio.CancelledError:
        raise
    except Exception:
        owner.failed = True
        logger.exception("Task admission owner connection lost")
        for task in tuple(owner.tasks):
            task.cancel()


@asynccontextmanager
async def admission_owner() -> AsyncIterator[_Owner]:
    global _OWNER
    task = asyncio.current_task()
    if task is None:
        raise RuntimeError("Task admission requires an async execution")
    async with _OWNER_GATE:
        if _OWNER is None:
            uri = postgres.uri()
            if uri is None:
                raise RuntimeError("Task admission requires PostgreSQL")
            engine = create_async_engine(uri, poolclass=NullPool)
            conn = await engine.connect()
            owner_id = uuid4()
            try:
                await conn.execute(
                    text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"),
                    {"key": f"task-owner:{owner_id}"},
                )
                await conn.commit()
            except BaseException:
                await conn.close()
                raise
            _OWNER = _Owner(owner_id, conn, set())
            _OWNER.monitor = asyncio.create_task(_watch_owner(_OWNER))
        owner = _OWNER
        if owner.failed:
            raise PermissionError("Task admission owner lost; wait for active tools to stop")
        owner.tasks.add(task)
    try:
        yield owner
    finally:
        async with _OWNER_GATE:
            owner.tasks.discard(task)
            if not owner.tasks:
                if owner.monitor:
                    owner.monitor.cancel()
                    await asyncio.gather(owner.monitor, return_exceptions=True)
                try:
                    await owner.connection.close()
                finally:
                    _OWNER = None


async def reclaim_admissions(conn: AsyncConnection, thread_id: str) -> None:
    owners = (
        (
            await conn.execute(
                text("SELECT DISTINCT owner_id FROM task_tool_admission WHERE thread_id = :thread"),
                {"thread": thread_id},
            )
        )
        .scalars()
        .all()
    )
    for owner_id in owners:
        if owner_id is None or (_OWNER is not None and _OWNER.id == owner_id):
            continue
        abandoned = await conn.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"task-owner:{owner_id}"},
        )
        if abandoned:
            await conn.execute(
                text(
                    "DELETE FROM task_tool_admission WHERE thread_id = :thread AND owner_id = :owner"
                ),
                {"thread": thread_id, "owner": owner_id},
            )


@dataclass(frozen=True)
class TaskRole:
    task_id: UUID
    coordinator_id: str
    role: str
    delegated: bool
    criteria: list[str]
    completed: bool


async def role(thread_id: str) -> TaskRole | None:
    if not postgres.configured():
        return None
    async with postgres.transaction() as conn:
        row = (
            await conn.execute(
                text("""
            SELECT t.id, t.coordinator_id, m.role, t.delegated,
                   t.acceptance_criteria, t.completed
            FROM coordinated_task t JOIN task_membership m ON m.task_id = t.id
            WHERE m.thread_id = :thread
        """),
                {"thread": thread_id},
            )
        ).first()
    return TaskRole(*row) if row else None


@asynccontextmanager
async def authority(
    thread_id: str, *, tool_call: bool = False, exclusive: bool = True
) -> AsyncIterator[None]:
    held = _HELD.get()
    if held is not None and held.active and held.thread_id == thread_id:
        if tool_call:
            raise PermissionError("Nested tool execution must pass through a new invocation")
        if exclusive and not held.exclusive:
            raise PermissionError("A tool admission cannot upgrade to a task transition")
        yield
        return
    if not postgres.configured():
        yield
        return
    async with admission_owner() as owner:
        admission_id = uuid4()
        async with postgres.transaction() as conn:
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"task-authority:{thread_id}"},
            )
            await reclaim_admissions(conn, thread_id)
            conflict = await conn.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM task_tool_admission "
                    "WHERE thread_id = :thread AND (:exclusive OR exclusive))"
                ),
                {"thread": thread_id, "exclusive": exclusive},
            )
            if conflict:
                raise PermissionError(
                    "Task transition conflicts with active tools; retry after they finish"
                )
            live = await conn.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"task-owner:{owner.id}"},
            )
            if live or owner.failed:
                raise PermissionError("Task admission owner connection lost")
            await conn.execute(
                text(
                    "INSERT INTO task_tool_admission (id, thread_id, exclusive, owner_id) "
                    "VALUES (:id, :thread, :exclusive, :owner)"
                ),
                {
                    "id": admission_id,
                    "thread": thread_id,
                    "exclusive": exclusive,
                    "owner": owner.id,
                },
            )
        lease = _Lease(thread_id, exclusive)
        token = _HELD.set(lease)
        try:
            yield
        finally:
            lease.active = False
            _HELD.reset(token)
            async with postgres.transaction() as conn:
                await conn.execute(
                    text("DELETE FROM task_tool_admission WHERE id = :id"), {"id": admission_id}
                )


async def ensure_task(thread_id: str) -> TaskRole:
    if not postgres.configured():
        raise ValueError("Task delegation requires PostgreSQL")
    current = await role(thread_id)
    if current:
        return current
    async with postgres.transaction() as conn:
        task_id = uuid4()
        await conn.execute(
            text("INSERT INTO coordinated_task(id, coordinator_id) VALUES (:id, :thread)"),
            {"id": task_id, "thread": thread_id},
        )
        await conn.execute(
            text("INSERT INTO task_membership VALUES (:thread, :id, 'coordinator')"),
            {"id": task_id, "thread": thread_id},
        )
    return TaskRole(task_id, thread_id, "coordinator", False, [], False)


async def assert_user_entry(thread_id: str) -> None:
    current = await role(thread_id)
    if current and current.role == "worker":
        raise HTTPException(403, f"Send task messages to coordinator {current.coordinator_id}")


async def update_task(
    thread_id: str, criteria: list[str], completed: bool, assessment: str
) -> None:
    async with authority(thread_id):
        current = await ensure_task(thread_id)
        if current.role != "coordinator":
            raise PermissionError("Only the coordinator maintains task acceptance criteria")
        if not criteria or any(not item.strip() for item in criteria):
            raise ValueError("Nonempty acceptance criteria are required")
        if completed and not assessment.strip():
            raise ValueError("Completion requires an assessment against the acceptance criteria")
        async with postgres.transaction() as conn:
            await conn.execute(
                text("""
                UPDATE coordinated_task SET acceptance_criteria = CAST(:criteria AS jsonb),
                    completed = :completed, assessment = :assessment WHERE id = :id
            """),
                {
                    "id": current.task_id,
                    "criteria": json.dumps(criteria),
                    "completed": completed,
                    "assessment": assessment,
                },
            )
