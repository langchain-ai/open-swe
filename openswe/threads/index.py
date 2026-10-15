"""The thread index in PostgreSQL: each thread's metadata, runtime and last known status.

With ``THREAD_STORE=postgres`` it answers thread reads, updates and searches in
the Agent Server's own shape, so callers of ``client.threads`` see no change.
Runs, checkpoints and streams stay with the thread's runtime.
"""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Final, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy import RowMapping, text

from openswe.database.postgres import transaction

Runtime = Literal["local", "mda"]
ThreadStatus = Literal["idle", "busy", "interrupted", "error"]
SortBy = Literal["thread_id", "status", "created_at", "updated_at"]
SelectField = Literal[
    "thread_id", "created_at", "updated_at", "metadata", "status", "values", "interrupts", "config"
]

THREAD_STATUSES: Final[frozenset[str]] = frozenset(get_args(ThreadStatus))
_COLUMNS: Final = "thread_id, runtime, metadata, status, created_at, updated_at"


class ThreadExistsError(Exception):
    """``create`` with ``if_exists="raise"`` found the thread already indexed."""


class IndexedThread(BaseModel):
    thread_id: str
    runtime: Runtime
    metadata: dict[str, JsonValue]
    status: ThreadStatus
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: RowMapping) -> Self:
        return cls.model_validate(dict(row))

    def as_langgraph(self, select: Sequence[SelectField] | None = None) -> dict[str, JsonValue]:
        """The thread as the Agent Server serves it; its state stays with the runtime."""
        thread: dict[str, JsonValue] = {
            "thread_id": self.thread_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "metadata": self.metadata,
            "status": self.status,
            "values": {},
            "interrupts": {},
            "config": {},
            "error": None,
        }
        return thread if not select else {key: thread[key] for key in select if key in thread}


class ThreadSearch(BaseModel):
    """The Agent Server's ``POST /threads/search`` body, as far as Open SWE uses it."""

    model_config = ConfigDict(extra="ignore")

    metadata: dict[str, JsonValue] = {}
    ids: list[str] | None = None
    status: ThreadStatus | None = None
    sort_by: SortBy = "created_at"
    sort_order: Literal["asc", "desc"] = "desc"
    limit: int = Field(default=10, ge=0)
    offset: int = Field(default=0, ge=0)
    select: list[SelectField] | None = None

    def _where(self) -> tuple[str, dict[str, object]]:
        clauses = ["metadata @> CAST(:filter AS jsonb)"]
        params: dict[str, object] = {"filter": json.dumps(self.metadata)}
        if self.ids is not None:
            clauses.append("thread_id = ANY(:ids)")
            params["ids"] = self.ids
        if self.status is not None:
            clauses.append("status = :status")
            params["status"] = self.status
        return " AND ".join(clauses), params

    async def run(self) -> list[IndexedThread]:
        where, params = self._where()
        order = "ASC" if self.sort_order == "asc" else "DESC"
        async with transaction() as conn:
            rows = (
                await conn.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM thread_index WHERE {where} "
                        f"ORDER BY {self.sort_by} {order}, thread_id {order} "
                        "LIMIT :limit OFFSET :offset"
                    ),
                    {**params, "limit": self.limit, "offset": self.offset},
                )
            ).mappings()
            return [IndexedThread.from_row(row) for row in rows]

    async def count(self) -> int:
        where, params = self._where()
        async with transaction() as conn:
            counted = await conn.scalar(
                text(f"SELECT count(*) FROM thread_index WHERE {where}"), params
            )
        return int(counted or 0)


async def create(
    thread_id: str,
    *,
    runtime: Runtime,
    metadata: Mapping[str, JsonValue] | None = None,
    if_exists: Literal["raise", "do_nothing"] = "raise",
    status: ThreadStatus = "idle",
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> IndexedThread:
    """Index a thread; with ``do_nothing`` an existing one is returned as it is."""
    async with transaction() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        f"""
                    INSERT INTO thread_index
                        (thread_id, runtime, metadata, status, created_at, updated_at)
                    VALUES (:thread_id, :runtime, CAST(:metadata AS jsonb), :status,
                            coalesce(:created_at, clock_timestamp()),
                            coalesce(:updated_at, :created_at, clock_timestamp()))
                    ON CONFLICT (thread_id) DO NOTHING
                    RETURNING {_COLUMNS}
                    """
                    ),
                    {
                        "thread_id": thread_id,
                        "runtime": runtime,
                        "metadata": json.dumps(dict(metadata or {})),
                        "status": status,
                        "created_at": created_at,
                        "updated_at": updated_at,
                    },
                )
            )
            .mappings()
            .first()
        )
    if row is not None:
        return IndexedThread.from_row(row)
    if if_exists == "raise":
        raise ThreadExistsError(thread_id)
    existing = await get(thread_id)
    if existing is None:
        raise ThreadExistsError(thread_id)
    return existing


async def get(thread_id: str) -> IndexedThread | None:
    async with transaction() as conn:
        row = (
            (
                await conn.execute(
                    text(f"SELECT {_COLUMNS} FROM thread_index WHERE thread_id = :thread_id"),
                    {"thread_id": thread_id},
                )
            )
            .mappings()
            .first()
        )
    return None if row is None else IndexedThread.from_row(row)


async def update(thread_id: str, metadata: Mapping[str, JsonValue]) -> IndexedThread | None:
    """Merge ``metadata`` into the thread's, key by key, as the Agent Server's PATCH does."""
    async with transaction() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        f"UPDATE thread_index SET metadata = metadata || CAST(:metadata AS jsonb), "
                        f"updated_at = clock_timestamp() WHERE thread_id = :thread_id "
                        f"RETURNING {_COLUMNS}"
                    ),
                    {"thread_id": thread_id, "metadata": json.dumps(dict(metadata))},
                )
            )
            .mappings()
            .first()
        )
    return None if row is None else IndexedThread.from_row(row)


async def set_status(thread_id: str, status: ThreadStatus) -> None:
    async with transaction() as conn:
        await conn.execute(
            text(
                "UPDATE thread_index SET status = :status, updated_at = clock_timestamp(), "
                "status_since = CASE WHEN status = :status THEN status_since "
                "ELSE clock_timestamp() END WHERE thread_id = :thread_id"
            ),
            {"thread_id": thread_id, "status": status},
        )


async def delete(thread_id: str) -> bool:
    async with transaction() as conn:
        result = await conn.execute(
            text("DELETE FROM thread_index WHERE thread_id = :thread_id"),
            {"thread_id": thread_id},
        )
    return bool(result.rowcount)


async def busy_since(before: datetime, *, limit: int = 200) -> list[IndexedThread]:
    """Threads marked busy since before ``before``: a run may have ended unannounced."""
    async with transaction() as conn:
        rows = (
            await conn.execute(
                text(
                    f"SELECT {_COLUMNS} FROM thread_index "
                    "WHERE status = 'busy' AND status_since < :before "
                    "ORDER BY status_since LIMIT :limit"
                ),
                {"before": before, "limit": limit},
            )
        ).mappings()
        return [IndexedThread.from_row(row) for row in rows]
