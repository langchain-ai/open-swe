"""The thread index in PostgreSQL: each thread's metadata, runtime and last known status.

``THREAD_STORE`` moves threads onto it in steps. With ``dual`` every thread
change is written here beside the Agent Server, which still answers thread
calls; with ``postgres`` the index answers thread reads, updates and searches
in the Agent Server's own shape, so callers of ``client.threads`` see no change,
and each change is still copied to the Agent Server. Runs, checkpoints and
streams stay with the thread's runtime.
"""

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import Final, Literal, Self, get_args
from uuid import uuid7

from langgraph_sdk.schema import ThreadSelectField
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError
from sqlalchemy import RowMapping, text

from openswe.config import ENV
from openswe.database.postgres import transaction
from openswe.remote_runtime.client import MDA_RUNTIME, RUNTIME_METADATA_KEY

Runtime = Literal["local", "mda"]
ThreadStatus = Literal["idle", "busy", "interrupted", "error"]
SortBy = Literal["thread_id", "status", "created_at", "updated_at"]
SelectField = Literal[
    "thread_id", "created_at", "updated_at", "metadata", "status", "values", "interrupts", "config"
]

ThreadStore = Literal["agent_server", "dual", "postgres"]

THREAD_STATUSES: Final[frozenset[str]] = frozenset(get_args(ThreadStatus))
# What the index keeps of an Agent Server thread; its values are its whole conversation.
INDEXED_FIELDS: Final[list[ThreadSelectField]] = [
    "thread_id",
    "metadata",
    "status",
    "created_at",
    "updated_at",
]
_COLUMNS: Final = "thread_id, runtime, metadata, status, created_at, updated_at"


_status = TypeAdapter(ThreadStatus)
_store = TypeAdapter(ThreadStore)


def thread_store() -> ThreadStore:
    return _store.validate_python(ENV.THREAD_STORE.get())


def index_kept() -> bool:
    """Whether thread changes are written to the index, whichever store answers reads."""
    return thread_store() != "agent_server"


def as_status(value: object) -> ThreadStatus | None:
    """``value`` as a thread status, or ``None`` when it is not one."""
    try:
        return _status.validate_python(value)
    except ValidationError:
        return None


_metadata = TypeAdapter(dict[str, JsonValue])


def _time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value) if isinstance(value, str) and value else None


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

    @classmethod
    def from_agent_server(cls, thread: Mapping[str, object]) -> Self | None:
        """The thread as the local Agent Server keeps it; ``None`` without its timestamps."""
        raw_metadata = thread.get("metadata")
        metadata = _metadata.validate_python(raw_metadata if isinstance(raw_metadata, dict) else {})
        created_at, updated_at = _time(thread.get("created_at")), _time(thread.get("updated_at"))
        thread_id = thread.get("thread_id")
        if created_at is None or updated_at is None or not isinstance(thread_id, str):
            return None
        return cls(
            thread_id=thread_id,
            runtime="mda" if metadata.get(RUNTIME_METADATA_KEY) == MDA_RUNTIME else "local",
            metadata=metadata,
            status=as_status(thread.get("status")) or "idle",
            created_at=created_at,
            updated_at=updated_at,
        )

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
                        (id, thread_id, runtime, metadata, status, created_at, updated_at)
                    VALUES (:id, :thread_id, :runtime, CAST(:metadata AS jsonb), :status,
                            coalesce(:created_at, clock_timestamp()),
                            coalesce(:updated_at, :created_at, clock_timestamp()))
                    ON CONFLICT (thread_id) DO NOTHING
                    RETURNING {_COLUMNS}
                    """
                    ),
                    {
                        "id": uuid7(),
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


async def import_newer(threads: Sequence[IndexedThread]) -> None:
    """Index threads as the Agent Server has them, except those the index has a newer copy of."""
    if not threads:
        return
    async with transaction() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO thread_index
                    (id, thread_id, runtime, metadata, status, created_at, updated_at)
                VALUES (:id, :thread_id, :runtime, CAST(:metadata AS jsonb), :status,
                        :created_at, :updated_at)
                ON CONFLICT (thread_id) DO UPDATE SET
                    runtime = EXCLUDED.runtime, metadata = EXCLUDED.metadata,
                    status = EXCLUDED.status, updated_at = EXCLUDED.updated_at
                WHERE thread_index.updated_at < EXCLUDED.updated_at
                """
            ),
            [
                {
                    "id": uuid7(),
                    **thread.model_dump(exclude={"metadata"}),
                    "metadata": json.dumps(thread.metadata),
                }
                for thread in threads
            ],
        )


async def sync(thread_id: str, read: Callable[[], Awaitable[IndexedThread | None]]) -> None:
    """Index a thread as ``read`` finds it on the Agent Server now, or drop it once gone.

    Syncs of one thread take turns, each reading inside its turn, so a sync that
    read an older copy can never land after one that read a newer copy. The
    status here is kept, since it follows runs the Agent Server does not see
    for threads that run on MDA.
    """
    async with transaction() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"thread_index:{thread_id}"},
        )
        thread = await read()
        if thread is None:
            await conn.execute(
                text("DELETE FROM thread_index WHERE thread_id = :thread_id"),
                {"thread_id": thread_id},
            )
            return
        await conn.execute(
            text(
                """
                INSERT INTO thread_index
                    (id, thread_id, runtime, metadata, status, created_at, updated_at)
                VALUES (:id, :thread_id, :runtime, CAST(:metadata AS jsonb), :status,
                        :created_at, :updated_at)
                ON CONFLICT (thread_id) DO UPDATE SET
                    runtime = EXCLUDED.runtime, metadata = EXCLUDED.metadata,
                    updated_at = greatest(thread_index.updated_at, EXCLUDED.updated_at)
                """
            ),
            {
                "id": uuid7(),
                **thread.model_dump(exclude={"metadata"}),
                "metadata": json.dumps(thread.metadata),
            },
        )


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


async def set_runtime(thread_id: str, runtime: Runtime) -> None:
    async with transaction() as conn:
        await conn.execute(
            text(
                "UPDATE thread_index SET runtime = :runtime, updated_at = clock_timestamp() "
                "WHERE thread_id = :thread_id"
            ),
            {"thread_id": thread_id, "runtime": runtime},
        )


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
