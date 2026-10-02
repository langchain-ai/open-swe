"""Durable task membership and execution authority."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text

from agent.database import postgres


@dataclass
class _Lease:
    thread_id: str
    active: bool = True


_HELD: ContextVar[_Lease | None] = ContextVar("task_authority", default=None)


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
async def authority(thread_id: str, *, tool_call: bool = False) -> AsyncIterator[None]:
    held = _HELD.get()
    if held is not None and held.active and held.thread_id == thread_id:
        if tool_call:
            raise PermissionError("Nested tool execution must pass through a new invocation")
        yield
        return
    if not postgres.configured():
        yield
        return
    while True:
        async with postgres.transaction() as conn:
            acquired = await conn.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"task-authority:{thread_id}"},
            )
            if acquired:
                lease = _Lease(thread_id)
                token = _HELD.set(lease)
                try:
                    yield
                finally:
                    lease.active = False
                    _HELD.reset(token)
                return
        await asyncio.sleep(0.05)


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
