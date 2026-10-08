"""Run each LangGraph Store import on one replica at a time until it is done.

An import keeps running at startup until a pass finds nothing left to move: a
pass that moved records gets one more, which catches what old replicas wrote
to the Store during the rolling deploy. ``store_import`` records each import's
progress, and its row lock keeps two replicas from running the same import.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import text

from openswe.database.postgres import transaction

logger = logging.getLogger(__name__)


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
                "moved = moved + :moved, waiting = :waiting, completed_at = CASE "
                "WHEN :moved = 0 AND :waiting = 0 THEN clock_timestamp() END WHERE name = :name"
            ),
            {"name": name, "moved": result.moved, "waiting": result.waiting},
        )
    logger.info(
        "Store import pass finished",
        extra={"store_import": name, "moved_records": result.moved, "waiting": result.waiting},
    )
