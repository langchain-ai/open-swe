"""Run each LangGraph Store import on one replica at a time until it is done.

An import keeps running at startup until a pass finds nothing left to move a
day after it last moved anything (or first ran). Old replicas keep writing to
the Store until a rollout finishes, so only a pass well after that may retire
the import. ``store_import`` records each import's progress, and its row lock
keeps two replicas from running the same import.
"""

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from openswe.database.postgres import transaction

logger = logging.getLogger(__name__)

# How long a PostgreSQL copy stays in place before its Store record may go: the
# same day ``run_store_import`` waits, long enough for any rollout to finish.
RETIRE_STORE_COPY_AFTER = timedelta(days=1)


def timestamp(value: object) -> datetime | None:
    """An ISO timestamp from a Store record as an aware datetime; an unreadable one reads as unset."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def store_copy_is_newer(
    stored: Mapping[str, object] | datetime | None, current: datetime | None
) -> bool:
    """Whether a Store record, or the time it was saved, is after the PostgreSQL copy last changed.

    A Store record whose time is unknown never wins.
    """
    stored_at = stored if not isinstance(stored, Mapping) else timestamp(stored.get("updated_at"))
    return stored_at is not None and (current is None or stored_at > current)


def may_delete_store_copy(copied_at: datetime | None) -> bool:
    """Whether a PostgreSQL copy written at ``copied_at`` has outlived every replica still reading the Store."""
    return copied_at is not None and datetime.now(UTC) - copied_at > RETIRE_STORE_COPY_AFTER


@dataclass(frozen=True)
class StoreImport:
    """What one pass of an import did."""

    moved: int = 0
    waiting: int = 0

    def __add__(self, other: StoreImport) -> StoreImport:
        return StoreImport(self.moved + other.moved, self.waiting + other.waiting)


async def run_store_import(name: str, run: Callable[[], Awaitable[StoreImport]]) -> None:
    """Run the ``name`` import unless it is done or another replica is running it."""
    async with transaction() as conn:
        await conn.execute(
            text("INSERT INTO store_import (name) VALUES (:name) ON CONFLICT DO NOTHING"),
            {"name": name},
        )
    async with transaction() as conn:
        claimed = await conn.scalar(
            text(
                "SELECT completed_at IS NULL FROM store_import WHERE name = :name "
                "FOR UPDATE SKIP LOCKED"
            ),
            {"name": name},
        )
        if not claimed:
            return
        result = await run()
        await conn.execute(
            text(
                "UPDATE store_import SET last_run_at = clock_timestamp(), "
                "moved = moved + :moved, waiting = :waiting, "
                "last_moved_at = CASE WHEN :moved > 0 THEN clock_timestamp() "
                "ELSE last_moved_at END, "
                "completed_at = CASE WHEN :moved = 0 AND :waiting = 0 "
                "AND last_moved_at < clock_timestamp() - interval '1 day' "
                "THEN clock_timestamp() END WHERE name = :name"
            ),
            {"name": name, "moved": result.moved, "waiting": result.waiting},
        )
    logger.info(
        "Store import pass finished",
        extra={"store_import": name, "moved_records": result.moved, "waiting": result.waiting},
    )
