"""The job queue: work due at a time, once or on a cron schedule, run by ``JobWorker``.

Background work and timers live here instead of in Agent Server runs and crons, so
they need no graph: a job names a ``kind``, whose handler the worker calls with
the job's ``payload``.
"""

import json
import logging
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Final, Self
from zoneinfo import ZoneInfo

from croniter import croniter
from pydantic import BaseModel, JsonValue
from sqlalchemy import RowMapping, text

from openswe.database.postgres import transaction

logger = logging.getLogger(__name__)

# How long a worker holds a claimed job; another replica takes it after that.
CLAIM_TTL: Final = timedelta(minutes=30)
MAX_ATTEMPTS: Final = 5
_RETRY_BASE: Final = timedelta(seconds=30)
_RETRY_CAP: Final = timedelta(hours=1)

_COLUMNS: Final = (
    "id, kind, payload, due_at, cron, timezone, key, metadata, attempts, created_at, claim_token"
)


# Set by every write the worker makes on its own: no person caused it.
_SYSTEM_WRITE: Final = "updated_at = clock_timestamp(), updated_by = NULL"


class Hold(Enum):
    """What a running job learns when it renews its claim."""

    HELD = "held"
    # Queued again under its key: this run finishes unrenewed, and the replacement stays.
    REPLACED = "replaced"
    CANCELLED = "cancelled"


class Job(BaseModel):
    id: uuid.UUID
    kind: str
    payload: dict[str, JsonValue]
    due_at: datetime
    cron: str | None
    timezone: str
    key: str | None
    metadata: dict[str, JsonValue]
    attempts: int
    created_at: datetime
    # The claim a worker's run holds this job under; ``None`` while it waits.
    claim_token: uuid.UUID | None = None
    # When this claim took the job over from one whose lease ran out: its worker
    # died or stalled mid-run.
    lapsed_lease: datetime | None = None

    @classmethod
    def from_row(cls, row: RowMapping) -> Self:
        return cls.model_validate(dict(row))

    @property
    def underway(self) -> bool:
        """Whether a worker holds this job or it is due for one now."""
        return self.claim_token is not None or self.due_at <= datetime.now(UTC)

    def next_fire_after(self, moment: datetime) -> datetime:
        if self.cron is None:
            raise ValueError("A one-shot job has no next fire time")
        local = moment.astimezone(ZoneInfo(self.timezone))
        return croniter(self.cron, local).get_next(datetime).astimezone(UTC)

    async def hold(self) -> Hold:
        """Renew this run's claim, unless the job was replaced or cancelled meanwhile."""
        async with transaction() as conn:
            renewed = (
                await conn.execute(
                    text(
                        "UPDATE job SET locked_until = clock_timestamp() + CAST(:ttl AS interval), "
                        f"{_SYSTEM_WRITE} WHERE id = :id AND claim_token = :token RETURNING 1"
                    ),
                    {"id": self.id, "token": self.claim_token, "ttl": CLAIM_TTL},
                )
            ).first()
            if renewed is not None:
                return Hold.HELD
            exists = await conn.scalar(text("SELECT 1 FROM job WHERE id = :id"), {"id": self.id})
        return Hold.REPLACED if exists else Hold.CANCELLED

    async def finish(self) -> None:
        """Delete a one-shot job, or move a cron job to its next fire time."""
        async with transaction() as conn:
            # A job queued again while this ran holds no claim of this run's, and stays.
            if self.cron is None:
                await conn.execute(
                    text("DELETE FROM job WHERE id = :id AND claim_token = :token"),
                    {"id": self.id, "token": self.claim_token},
                )
                return
            await conn.execute(
                text(
                    "UPDATE job SET due_at = :due_at, attempts = 0, locked_until = NULL, "
                    f"claim_token = NULL, {_SYSTEM_WRITE} WHERE id = :id AND claim_token = :token"
                ),
                {
                    "id": self.id,
                    "token": self.claim_token,
                    "due_at": self.next_fire_after(datetime.now(UTC)),
                },
            )

    async def stop(self) -> None:
        """Delete this job for good, unless it was queued again while this run held it."""
        async with transaction() as conn:
            await conn.execute(
                text("DELETE FROM job WHERE id = :id AND claim_token = :token"),
                {"id": self.id, "token": self.claim_token},
            )

    async def retry_later(self) -> None:
        """After a failure: back off a one-shot job, or skip a cron job to its next fire time."""
        if self.cron is not None:
            await self.finish()
            return
        attempts = self.attempts + 1
        if attempts >= MAX_ATTEMPTS:
            logger.error(
                "Job gave up after repeated failures",
                extra={"job_id": str(self.id), "job_kind": self.kind, "attempts": attempts},
            )
            await self.finish()
            return
        delay = min(_RETRY_BASE * 2 ** (attempts - 1), _RETRY_CAP)
        async with transaction() as conn:
            await conn.execute(
                text(
                    "UPDATE job SET due_at = clock_timestamp() + CAST(:delay AS interval), "
                    "attempts = :attempts, locked_until = NULL, claim_token = NULL, "
                    f"{_SYSTEM_WRITE} WHERE id = :id AND claim_token = :token"
                ),
                {"id": self.id, "token": self.claim_token, "delay": delay, "attempts": attempts},
            )


async def schedule(
    kind: str,
    payload: Mapping[str, JsonValue] | None = None,
    *,
    due_at: datetime | None = None,
    cron: str | None = None,
    timezone: str = "UTC",
    key: str | None = None,
    metadata: Mapping[str, JsonValue] | None = None,
    by: uuid.UUID | None = None,
    replace: bool = True,
) -> Job:
    """Queue a job; one with ``key`` replaces the job queued under that key before.

    With ``replace=False``, an existing keyed job keeps its payload, schedule and claim.
    ``by`` is the person whose action queued it, ``None`` for the system's own work.
    """
    if cron is not None and not croniter.is_valid(cron):
        raise ValueError(f"Invalid cron schedule: {cron}")
    job = Job(
        id=uuid.uuid7(),
        kind=kind,
        payload=dict(payload or {}),
        due_at=due_at or datetime.now(UTC),
        cron=cron,
        timezone=timezone,
        key=key,
        metadata=dict(metadata or {}),
        attempts=0,
        created_at=datetime.now(UTC),
    )
    if cron is not None and due_at is None:
        job.due_at = job.next_fire_after(datetime.now(UTC))
    updates = (
        "kind = EXCLUDED.kind, payload = EXCLUDED.payload, "
        "due_at = EXCLUDED.due_at, cron = EXCLUDED.cron, "
        "timezone = EXCLUDED.timezone, metadata = EXCLUDED.metadata, "
        "attempts = 0, locked_until = NULL, claim_token = NULL, "
        "updated_at = clock_timestamp(), updated_by = EXCLUDED.updated_by"
        if replace
        else "key = EXCLUDED.key"
    )
    async with transaction() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        f"""
                    INSERT INTO job (id, kind, payload, due_at, cron, timezone, key, metadata,
                                     created_by, updated_by)
                    VALUES (:id, :kind, CAST(:payload AS jsonb), :due_at, :cron, :timezone,
                            :key, CAST(:metadata AS jsonb), :by, :by)
                    ON CONFLICT (key) DO UPDATE SET {updates}
                    RETURNING {_COLUMNS}
                    """
                    ),
                    {
                        **job.model_dump(
                            include={"id", "kind", "due_at", "cron", "timezone", "key"}
                        ),
                        "payload": json.dumps(job.payload),
                        "metadata": json.dumps(job.metadata),
                        "by": by,
                    },
                )
            )
            .mappings()
            .one()
        )
    return Job.from_row(row)


async def find(metadata: Mapping[str, JsonValue], *, limit: int = 100) -> list[Job]:
    """Jobs whose metadata contains ``metadata``."""
    async with transaction() as conn:
        rows = (
            await conn.execute(
                text(
                    f"SELECT {_COLUMNS} FROM job WHERE metadata @> CAST(:filter AS jsonb) "
                    "ORDER BY due_at LIMIT :limit"
                ),
                {"filter": json.dumps(dict(metadata)), "limit": limit},
            )
        ).mappings()
        return [Job.from_row(row) for row in rows]


async def get(key: str) -> Job | None:
    async with transaction() as conn:
        row = (
            (await conn.execute(text(f"SELECT {_COLUMNS} FROM job WHERE key = :key"), {"key": key}))
            .mappings()
            .first()
        )
    return None if row is None else Job.from_row(row)


async def run_now(key: str, *, by: uuid.UUID | None = None) -> None:
    """Make the job queued under ``key`` due now; one a worker holds is already running."""
    async with transaction() as conn:
        await conn.execute(
            text(
                "UPDATE job SET due_at = least(due_at, clock_timestamp()), "
                "updated_at = clock_timestamp(), updated_by = :by "
                "WHERE key = :key AND claim_token IS NULL"
            ),
            {"key": key, "by": by},
        )


async def cancel(job_id: uuid.UUID) -> bool:
    """Cancel a job; whether there was one."""
    async with transaction() as conn:
        result = await conn.execute(text("DELETE FROM job WHERE id = :id"), {"id": job_id})
    return bool(result.rowcount)


async def cancel_key(key: str) -> None:
    async with transaction() as conn:
        await conn.execute(text("DELETE FROM job WHERE key = :key"), {"key": key})


async def cancel_matching(metadata: Mapping[str, JsonValue]) -> int:
    """Cancel every job whose metadata contains ``metadata``; how many there were."""
    async with transaction() as conn:
        result = await conn.execute(
            text("DELETE FROM job WHERE metadata @> CAST(:filter AS jsonb)"),
            {"filter": json.dumps(dict(metadata))},
        )
        return result.rowcount


async def claim_due(limit: int) -> list[Job]:
    """Take up to ``limit`` due jobs no other worker holds."""
    async with transaction() as conn:
        rows = (
            await conn.execute(
                text(
                    f"""
                    WITH due AS (
                        SELECT id, locked_until AS lapsed_lease FROM job
                        WHERE due_at <= clock_timestamp()
                            AND (locked_until IS NULL OR locked_until < clock_timestamp())
                        ORDER BY due_at
                        LIMIT :limit
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE job SET locked_until = clock_timestamp() + CAST(:ttl AS interval),
                        claim_token = gen_random_uuid(), {_SYSTEM_WRITE}
                    FROM due WHERE job.id = due.id
                    RETURNING {", ".join(f"job.{c.strip()}" for c in _COLUMNS.split(","))},
                        due.lapsed_lease
                    """
                ),
                {"limit": limit, "ttl": CLAIM_TTL},
            )
        ).mappings()
        return [Job.from_row(row) for row in rows]
